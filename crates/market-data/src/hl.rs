//! Parsing Hyperliquid WebSocket messages into normalized events.
//!
//! Deliberately **tolerant**: unknown channels and extra fields are ignored,
//! because an exchange may add fields at any time and a feed handler that
//! falls over on additive changes is an outage waiting to happen. This is the
//! exact opposite of config parsing (fail-closed) — the asymmetry is by
//! design: we control configs, we do not control the exchange.
//!
//! Schemas verified against the Hyperliquid API docs (2026-06-11): l2Book is
//! a full-snapshot feed (`levels: [bids, asks]`, px/sz as decimal strings,
//! `time` in ms); trades carry `side` `"B"` (buy aggressor) / `"A"` (sell
//! aggressor); heartbeat is `{"method":"ping"}` against a 60s server timeout.

use quantis_core::events::{L2Snapshot, Level, Trade};
use quantis_core::types::{Side, TsNanos};
use serde::Deserialize;
use thiserror::Error;

/// Errors turning an exchange message into normalized events.
#[derive(Debug, Error)]
pub enum HlParseError {
    /// Not valid JSON or missing required structure.
    #[error("hyperliquid message JSON: {0}")]
    Json(#[from] serde_json::Error),
    /// A price or size string failed fixed-point parsing.
    #[error("bad decimal {0:?}")]
    Decimal(String),
    /// An unknown trade side code.
    #[error("unknown side code {0:?}")]
    SideCode(String),
}

/// A classified Hyperliquid message.
#[derive(Debug)]
pub enum HlMessage {
    /// An l2Book snapshot.
    Book(L2Snapshot),
    /// A batch of trade prints.
    Trades(Vec<Trade>),
    /// Subscription acknowledgement.
    SubscriptionAck,
    /// Heartbeat response.
    Pong,
    /// A channel we do not consume; ignored by policy.
    Other,
}

#[derive(Deserialize)]
struct Envelope {
    channel: String,
    #[serde(default)]
    data: serde_json::Value,
}

#[derive(Deserialize)]
struct WsBook {
    time: i64,
    levels: (Vec<WsLevel>, Vec<WsLevel>),
}

#[derive(Deserialize)]
struct WsLevel {
    px: String,
    sz: String,
    n: u32,
}

#[derive(Deserialize)]
struct WsTrade {
    side: String,
    px: String,
    sz: String,
    time: i64,
    tid: u64,
}

/// Parse one raw WebSocket text message, stamping `recv_ts` on any events.
pub fn parse_message(text: &str, recv_ts: TsNanos) -> Result<HlMessage, HlParseError> {
    let env: Envelope = serde_json::from_str(text)?;
    match env.channel.as_str() {
        "l2Book" => {
            let raw: WsBook = serde_json::from_value(env.data)?;
            Ok(HlMessage::Book(L2Snapshot {
                exch_ts: TsNanos::from_millis(raw.time),
                recv_ts,
                bids: convert_levels(raw.levels.0)?,
                asks: convert_levels(raw.levels.1)?,
            }))
        }
        "trades" => {
            let raw: Vec<WsTrade> = serde_json::from_value(env.data)?;
            let mut trades = Vec::with_capacity(raw.len());
            for t in raw {
                trades.push(Trade {
                    px: parse_decimal(&t.px)?,
                    qty: parse_decimal(&t.sz)?,
                    side: Side::from_hl(&t.side).ok_or(HlParseError::SideCode(t.side))?,
                    exch_ts: TsNanos::from_millis(t.time),
                    recv_ts,
                    tid: t.tid,
                });
            }
            Ok(HlMessage::Trades(trades))
        }
        "subscriptionResponse" => Ok(HlMessage::SubscriptionAck),
        "pong" => Ok(HlMessage::Pong),
        _ => Ok(HlMessage::Other),
    }
}

fn convert_levels(raw: Vec<WsLevel>) -> Result<Vec<Level>, HlParseError> {
    let mut out = Vec::with_capacity(raw.len());
    for l in raw {
        out.push(Level {
            px: parse_decimal(&l.px)?,
            qty: parse_decimal(&l.sz)?,
            n_orders: l.n,
        });
    }
    Ok(out)
}

fn parse_decimal<T: std::str::FromStr>(s: &str) -> Result<T, HlParseError> {
    s.parse().map_err(|_| HlParseError::Decimal(s.to_owned()))
}

/// The subscribe message for a channel type and coin.
pub fn subscribe_json(channel: &str, coin: &str) -> String {
    format!(r#"{{"method":"subscribe","subscription":{{"type":"{channel}","coin":"{coin}"}}}}"#)
}

/// The heartbeat message; must reach the server inside its 60s idle timeout.
pub const PING_JSON: &str = r#"{"method":"ping"}"#;

#[cfg(test)]
mod tests {
    use super::*;

    const BOOK_MSG: &str = r#"{
      "channel": "l2Book",
      "data": {
        "coin": "BTC",
        "time": 1700000000123,
        "levels": [
          [{"px": "100000.0", "sz": "1.5", "n": 3}, {"px": "99999.0", "sz": "2.0", "n": 1}],
          [{"px": "100001.0", "sz": "0.5", "n": 2}]
        ]
      }
    }"#;

    const TRADES_MSG: &str = r#"{
      "channel": "trades",
      "data": [
        {"coin": "BTC", "side": "B", "px": "100000.5", "sz": "0.01",
         "hash": "0xabc", "time": 1700000000456, "tid": 123456789,
         "users": ["0x1", "0x2"]}
      ]
    }"#;

    #[test]
    fn parses_l2book_snapshot() {
        let recv = TsNanos::from_millis(1_700_000_000_200);
        let msg = parse_message(BOOK_MSG, recv).unwrap();
        let HlMessage::Book(snap) = msg else {
            panic!("expected book")
        };
        assert_eq!(snap.exch_ts, TsNanos::from_millis(1_700_000_000_123));
        assert_eq!(snap.recv_ts, recv);
        assert_eq!(snap.bids.len(), 2);
        assert_eq!(snap.asks.len(), 1);
        assert_eq!(snap.bids[0].px, "100000".parse().unwrap());
        assert_eq!(snap.bids[0].n_orders, 3);
    }

    #[test]
    fn parses_trades_with_extra_fields_ignored() {
        let recv = TsNanos::from_millis(1_700_000_000_500);
        let HlMessage::Trades(trades) = parse_message(TRADES_MSG, recv).unwrap() else {
            panic!("expected trades")
        };
        assert_eq!(trades.len(), 1);
        assert_eq!(trades[0].side, Side::Buy);
        assert_eq!(trades[0].px, "100000.5".parse().unwrap());
        assert_eq!(trades[0].tid, 123_456_789);
    }

    #[test]
    fn unknown_channels_are_ignored_not_fatal() {
        let msg = r#"{"channel": "somethingNew", "data": {"x": 1}}"#;
        assert!(matches!(
            parse_message(msg, TsNanos::from_millis(0)).unwrap(),
            HlMessage::Other
        ));
    }

    #[test]
    fn acks_and_pongs_are_classified() {
        let ack = r#"{"channel":"subscriptionResponse","data":{"method":"subscribe","subscription":{"type":"trades","coin":"BTC"}}}"#;
        assert!(matches!(
            parse_message(ack, TsNanos::from_millis(0)).unwrap(),
            HlMessage::SubscriptionAck
        ));
        let pong = r#"{"channel":"pong"}"#;
        assert!(matches!(
            parse_message(pong, TsNanos::from_millis(0)).unwrap(),
            HlMessage::Pong
        ));
    }

    #[test]
    fn bad_decimals_are_loud() {
        let msg =
            r#"{"channel":"trades","data":[{"side":"B","px":"oops","sz":"1","time":0,"tid":1}]}"#;
        assert!(matches!(
            parse_message(msg, TsNanos::from_millis(0)),
            Err(HlParseError::Decimal(_))
        ));
    }
}
