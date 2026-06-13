//! The matching/fill engine — single source of truth for fills.
//!
//! Two paths, both here and both reused by the paper gateway (ADR-004):
//!
//! - [`FillEngine::market`]: a market order walks the visible ladder of the
//!   most recent L2 snapshot and pays the taker fee; depth it cannot absorb is
//!   reported, never invented. Latency and funding are applied by the engine
//!   loop around it.
//! - [`FillEngine::match_resting`]: a resting limit order fills via the
//!   **conservative back-of-queue** model — adverse trades first clear the
//!   queue that was ahead of it, then fill it at its price (no improvement)
//!   with the maker fee. With snapshot data true queue position is
//!   unobservable, so "last in line" is the honest default.

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

/// A resting limit order matched by the **conservative back-of-queue** model
/// (ADR-004): the order joins the back of the queue at its price, so all size
/// that was ahead of it at placement must trade through before it fills. With
/// snapshot data we cannot observe true queue position, so assuming we are last
/// in line is the honest default — it never over-credits a maker fill.
#[derive(Debug, Clone, Copy)]
pub struct RestingOrder {
    /// Side of the resting order.
    pub side: Side,
    /// Resting price.
    pub px: Px,
    /// Total order size.
    pub qty: Qty,
    /// Quantity filled so far.
    pub filled: Qty,
    /// Remaining size ahead of us in the queue (decremented by adverse trades).
    pub queue_ahead: Qty,
}

impl RestingOrder {
    /// A new resting order behind `queue_ahead` of existing size at its level.
    pub fn new(side: Side, px: Px, qty: Qty, queue_ahead: Qty) -> Self {
        Self {
            side,
            px,
            qty,
            filled: Qty::ZERO,
            queue_ahead,
        }
    }

    /// Remaining unfilled quantity.
    pub fn remaining(&self) -> Qty {
        self.qty - self.filled
    }

    /// True once fully filled.
    pub fn is_done(&self) -> bool {
        self.remaining().raw() <= 0
    }
}

impl FillEngine {
    /// Apply one trade print to a resting order under the queue model; returns a
    /// maker [`Fill`] if the order (partially) fills.
    ///
    /// A resting buy is filled only by sell-aggressor trades at or below its
    /// price (and vice-versa); the trade volume first clears the remaining
    /// queue ahead, and only the surplus fills the order, at the resting price
    /// (no improvement) with the maker fee.
    pub fn match_resting(
        &self,
        order: &mut RestingOrder,
        trade: &quantis_core::events::Trade,
    ) -> Option<Fill> {
        let eligible = match order.side {
            Side::Buy => trade.side == Side::Sell && trade.px.raw() <= order.px.raw(),
            Side::Sell => trade.side == Side::Buy && trade.px.raw() >= order.px.raw(),
        };
        if !eligible {
            return None;
        }
        let mut vol = trade.qty.raw();
        // Clear the queue ahead first (conservative: we are last in line).
        if order.queue_ahead.raw() > 0 {
            let consumed = vol.min(order.queue_ahead.raw());
            order.queue_ahead = Qty::from_raw(order.queue_ahead.raw() - consumed);
            vol -= consumed;
        }
        let remaining = order.remaining().raw();
        let take = vol.min(remaining);
        if take <= 0 {
            return None;
        }
        let take = Qty::from_raw(take);
        order.filled += take;
        let notional = order.px.notional(take);
        let fee = notional.fee_ppm(self.params.maker_fee_ppm);
        Some(Fill {
            side: order.side,
            px: order.px,
            qty: take,
            notional,
            fee,
            exch_ts: trade.exch_ts,
        })
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

    fn trade(side: Side, px: &str, qty: &str) -> quantis_core::events::Trade {
        quantis_core::events::Trade {
            px: px.parse().unwrap(),
            qty: qty.parse().unwrap(),
            side,
            exch_ts: TsNanos::from_millis(2_000),
            recv_ts: TsNanos::from_millis(2_001),
            tid: 1,
        }
    }

    #[test]
    fn resting_buy_waits_for_queue_then_fills_at_maker_fee() {
        // Resting buy 1.0 @ 100, behind 2.0 of queue.
        let mut order = RestingOrder::new(
            Side::Buy,
            "100".parse().unwrap(),
            "1".parse().unwrap(),
            "2".parse().unwrap(),
        );
        // A sell trade of 1.5 only eats queue (2.0 -> 0.5), no fill yet.
        assert!(
            engine()
                .match_resting(&mut order, &trade(Side::Sell, "99", "1.5"))
                .is_none()
        );
        assert_eq!(order.filled, Qty::ZERO);
        // A sell trade of 1.0 clears the remaining 0.5 queue, then fills 0.5.
        let fill = engine()
            .match_resting(&mut order, &trade(Side::Sell, "100", "1.0"))
            .unwrap();
        assert_eq!(fill.qty, "0.5".parse().unwrap());
        assert_eq!(fill.px, "100".parse().unwrap()); // no improvement
        // maker fee = 150 ppm on 100*0.5 = 50 -> 0.0075
        assert_eq!(fill.fee, "0.0075".parse().unwrap());
    }

    #[test]
    fn resting_order_ignores_wrong_side_and_wrong_price() {
        let mut order = RestingOrder::new(
            Side::Buy,
            "100".parse().unwrap(),
            "1".parse().unwrap(),
            Qty::ZERO,
        );
        // a BUY-aggressor trade cannot fill a resting BUY
        assert!(
            engine()
                .match_resting(&mut order, &trade(Side::Buy, "100", "1"))
                .is_none()
        );
        // a sell ABOVE our price does not reach us
        assert!(
            engine()
                .match_resting(&mut order, &trade(Side::Sell, "101", "1"))
                .is_none()
        );
        assert_eq!(order.filled, Qty::ZERO);
    }

    #[test]
    fn resting_order_never_overfills() {
        let mut order = RestingOrder::new(
            Side::Sell,
            "100".parse().unwrap(),
            "1".parse().unwrap(),
            Qty::ZERO,
        );
        // a huge buy trade fills only the remaining 1.0, not more
        let fill = engine()
            .match_resting(&mut order, &trade(Side::Buy, "100", "10"))
            .unwrap();
        assert_eq!(fill.qty, "1".parse().unwrap());
        assert!(order.is_done());
    }
}
