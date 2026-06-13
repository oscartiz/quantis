//! The matching/fill engine — single source of truth for fills.
//!
//! Phase 1 (v0) scope, stated plainly: **market orders only**, filled by
//! walking the visible ladder of the most recent L2 snapshot, with zero
//! submission latency and taker fees. There is no queue modelling, no
//! funding, and no latency injection yet — Phase 3 adds them (ADR-004).
//! Until then, backtest results are optimistic by construction, and every
//! artifact this engine produces is interpreted with that caveat.

use quantis_core::types::{Cash, Px, Qty, Side, TsNanos};
use quantis_market_data::book::OrderBook;

/// Fee schedule in parts-per-million of notional.
#[derive(Debug, Clone, Copy)]
pub struct FillParams {
    /// Taker fee ppm (crossing the spread).
    pub taker_fee_ppm: i64,
    /// Maker fee ppm (resting orders; unused until Phase 3 adds limit fills).
    pub maker_fee_ppm: i64,
}

/// One execution against one price level.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Fill {
    /// Order side.
    pub side: Side,
    /// Execution price (the level's price; no improvement modelled).
    pub px: Px,
    /// Executed quantity (positive).
    pub qty: Qty,
    /// `px * qty`, positive.
    pub notional: Cash,
    /// Fee charged, positive.
    pub fee: Cash,
    /// Timestamp of the event that triggered the order.
    pub exch_ts: TsNanos,
}

/// Result of a market order: fills plus any quantity the visible book could
/// not absorb (reported, never silently dropped).
#[derive(Debug, Clone, PartialEq)]
pub struct MarketFillOutcome {
    /// Executions, best level first.
    pub fills: Vec<Fill>,
    /// Quantity that found no visible liquidity.
    pub unfilled: Qty,
}

/// Stateless matcher over a book reference.
#[derive(Debug, Clone, Copy)]
pub struct FillEngine {
    params: FillParams,
}

impl FillEngine {
    /// Build with a fee schedule.
    pub fn new(params: FillParams) -> Self {
        Self { params }
    }

    /// Execute a market order by walking the visible opposite ladder.
    /// `qty` must be positive; direction comes from `side`.
    pub fn market(
        &self,
        side: Side,
        qty: Qty,
        book: &OrderBook,
        exch_ts: TsNanos,
    ) -> MarketFillOutcome {
        debug_assert!(qty.raw() > 0, "market() takes positive qty");
        let ladder = match side {
            Side::Buy => book.asks(),
            Side::Sell => book.bids(),
        };
        let mut remaining = qty;
        let mut fills = Vec::new();
        for level in ladder {
            if remaining.raw() <= 0 {
                break;
            }
            let take = if level.qty.raw() < remaining.raw() {
                level.qty
            } else {
                remaining
            };
            let notional = level.px.notional(take);
            let fee = notional.fee_ppm(self.params.taker_fee_ppm);
            fills.push(Fill {
                side,
                px: level.px,
                qty: take,
                notional,
                fee,
                exch_ts,
            });
            remaining -= take;
        }
        MarketFillOutcome {
            fills,
            unfilled: remaining,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use quantis_core::events::{L2Snapshot, Level};

    fn book() -> OrderBook {
        let mut b = OrderBook::new();
        b.apply_snapshot(&L2Snapshot {
            exch_ts: TsNanos::from_millis(1_000),
            recv_ts: TsNanos::from_millis(1_001),
            bids: vec![
                Level {
                    px: "99".parse().unwrap(),
                    qty: "1".parse().unwrap(),
                    n_orders: 1,
                },
                Level {
                    px: "98".parse().unwrap(),
                    qty: "2".parse().unwrap(),
                    n_orders: 1,
                },
            ],
            asks: vec![
                Level {
                    px: "101".parse().unwrap(),
                    qty: "1".parse().unwrap(),
                    n_orders: 1,
                },
                Level {
                    px: "102".parse().unwrap(),
                    qty: "2".parse().unwrap(),
                    n_orders: 1,
                },
            ],
        });
        b
    }

    fn engine() -> FillEngine {
        FillEngine::new(FillParams {
            taker_fee_ppm: 450,
            maker_fee_ppm: 150,
        })
    }

    #[test]
    fn buy_walks_ask_levels_with_exact_fees() {
        let outcome = engine().market(
            Side::Buy,
            "1.5".parse().unwrap(),
            &book(),
            TsNanos::from_millis(1_000),
        );
        assert_eq!(outcome.unfilled, Qty::ZERO);
        assert_eq!(outcome.fills.len(), 2);
        assert_eq!(outcome.fills[0].px, "101".parse().unwrap());
        assert_eq!(outcome.fills[0].qty, "1".parse().unwrap());
        assert_eq!(outcome.fills[1].px, "102".parse().unwrap());
        assert_eq!(outcome.fills[1].qty, "0.5".parse().unwrap());
        // fee on 101 * 1 at 450 ppm = 0.04545
        assert_eq!(outcome.fills[0].fee, "0.04545".parse().unwrap());
        // fee on 102 * 0.5 = 51 at 450 ppm = 0.02295
        assert_eq!(outcome.fills[1].fee, "0.02295".parse().unwrap());
    }

    #[test]
    fn insufficient_depth_is_reported_not_invented() {
        let outcome = engine().market(
            Side::Sell,
            "10".parse().unwrap(),
            &book(),
            TsNanos::from_millis(1_000),
        );
        // visible bids hold 3 total
        assert_eq!(outcome.unfilled, "7".parse().unwrap());
        assert_eq!(outcome.fills.len(), 2);
    }
}
