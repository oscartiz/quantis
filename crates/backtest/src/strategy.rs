//! The strategy interface and the SMA-cross plumbing demo.
//!
//! A strategy sees every normalized event plus the current book and its own
//! position, and emits order intents. It cannot see the future: the engine
//! applies the book update for the event *before* the strategy runs, and
//! fills happen against that same visible state — the only data a live
//! strategy would have at that moment.

use std::collections::VecDeque;

use quantis_core::events::MarketEvent;
use quantis_core::types::{Qty, Side};
use quantis_market_data::book::OrderBook;

/// Whether an intent crosses the spread now (market) or rests at a price
/// (limit). Limit orders fill via the conservative queue model (ADR-004).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IntentKind {
    /// Cross the spread immediately (taker).
    Market,
    /// Rest at this price until enough adverse volume clears the queue (maker).
    Limit(quantis_core::types::Px),
}

/// An order request from a strategy.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct OrderIntent {
    /// Direction.
    pub side: Side,
    /// Size (positive).
    pub qty: Qty,
    /// Market or resting-limit.
    pub kind: IntentKind,
}

/// Collector for a strategy's order intents during one event.
#[derive(Debug, Default)]
pub struct Actions {
    orders: Vec<OrderIntent>,
}

impl Actions {
    /// Request a market order (crosses the spread).
    pub fn market(&mut self, side: Side, qty: Qty) {
        debug_assert!(qty.raw() > 0, "market() takes positive qty");
        self.orders.push(OrderIntent {
            side,
            qty,
            kind: IntentKind::Market,
        });
    }

    /// Request a resting limit order at `px` (maker).
    pub fn limit(&mut self, side: Side, qty: Qty, px: quantis_core::types::Px) {
        debug_assert!(qty.raw() > 0, "limit() takes positive qty");
        self.orders.push(OrderIntent {
            side,
            qty,
            kind: IntentKind::Limit(px),
        });
    }

    /// Drain accumulated intents (engine-side).
    pub fn take(&mut self) -> Vec<OrderIntent> {
        std::mem::take(&mut self.orders)
    }
}

/// A trading strategy driven by the event loop.
pub trait Strategy {
    /// Handle one event. `position` is the engine's actual position after all
    /// previous fills (strategies must not assume their orders filled fully).
    fn on_event(
        &mut self,
        event: &MarketEvent,
        book: &OrderBook,
        position: Qty,
        actions: &mut Actions,
    );
}

/// SMA crossover on the L2 mid-price.
///
/// **Plumbing demo, not alpha**: it exists to exercise the engine
/// deterministically end to end. Signal: when the fast SMA of the mid crosses
/// above the slow SMA, target `+order_qty`; below, `-order_qty`. All integer
/// arithmetic (sums compared via cross-multiplication), so runs are
/// bit-reproducible.
#[derive(Debug)]
pub struct SmaCross {
    fast_n: usize,
    slow_n: usize,
    order_qty: Qty,
    window: VecDeque<i64>,
    fast_sum: i128,
    slow_sum: i128,
    last_above: Option<bool>,
}

impl SmaCross {
    /// Windows are counted in L2 snapshots; `fast < slow` is enforced by
    /// config validation.
    pub fn new(fast: u32, slow: u32, order_qty: Qty) -> Self {
        Self {
            fast_n: fast as usize,
            slow_n: slow as usize,
            order_qty,
            window: VecDeque::with_capacity(slow as usize + 1),
            fast_sum: 0,
            slow_sum: 0,
            last_above: None,
        }
    }
}

impl Strategy for SmaCross {
    fn on_event(
        &mut self,
        event: &MarketEvent,
        book: &OrderBook,
        position: Qty,
        actions: &mut Actions,
    ) {
        let MarketEvent::L2Snapshot(_) = event else {
            return;
        };
        let Some(mid) = book.mid() else { return };

        let m = i128::from(mid.raw());
        self.window.push_back(mid.raw());
        self.fast_sum += m;
        self.slow_sum += m;
        if self.window.len() > self.fast_n {
            let leaving = self.window[self.window.len() - 1 - self.fast_n];
            self.fast_sum -= i128::from(leaving);
        }
        if self.window.len() > self.slow_n {
            let old = self.window.pop_front().expect("len > slow_n");
            self.slow_sum -= i128::from(old);
        }
        if self.window.len() < self.slow_n {
            return;
        }

        // fast_sum/fast_n > slow_sum/slow_n, integer-exact.
        let fast_above = self.fast_sum * self.slow_n as i128 > self.slow_sum * self.fast_n as i128;
        if self.last_above == Some(fast_above) {
            return;
        }
        self.last_above = Some(fast_above);

        let target = if fast_above {
            self.order_qty
        } else {
            -self.order_qty
        };
        let delta = target - position;
        if delta.raw() > 0 {
            actions.market(Side::Buy, delta);
        } else if delta.raw() < 0 {
            actions.market(Side::Sell, delta.abs());
        }
    }
}

/// A passive (maker) ping-pong strategy: when flat it rests a buy at the best
/// bid; once filled (long) it rests a sell at the best ask; repeat. It holds at
/// most one resting order at a time, so it needs no cancellation, and it exists
/// to exercise the conservative back-of-queue maker fill model (ADR-004) end to
/// end — not as alpha. Maker orders pay maker fees and fill only as adverse
/// trades clear the queue ahead of them.
#[derive(Debug)]
pub struct PassiveMaker {
    order_qty: Qty,
    last_position: Qty,
    outstanding: bool,
}

impl PassiveMaker {
    /// New maker quoting `order_qty` per side.
    pub fn new(order_qty: Qty) -> Self {
        Self {
            order_qty,
            last_position: Qty::ZERO,
            outstanding: false,
        }
    }
}

impl Strategy for PassiveMaker {
    fn on_event(
        &mut self,
        event: &MarketEvent,
        book: &OrderBook,
        position: Qty,
        actions: &mut Actions,
    ) {
        let MarketEvent::L2Snapshot(_) = event else {
            return;
        };
        // A position change means our resting order (partially) filled.
        if position != self.last_position {
            self.last_position = position;
            self.outstanding = false;
        }
        if self.outstanding {
            return;
        }
        let (Some(bid), Some(ask)) = (book.best_bid(), book.best_ask()) else {
            return;
        };
        if position.raw() == 0 {
            actions.limit(Side::Buy, self.order_qty, bid.px); // join the bid queue
            self.outstanding = true;
        } else if position.raw() >= self.order_qty.raw() {
            actions.limit(Side::Sell, self.order_qty, ask.px); // rest an exit at the ask
            self.outstanding = true;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use quantis_core::events::{L2Snapshot, Level};
    use quantis_core::types::{Px, TsNanos};

    fn snapshot_at(mid_raw: i64, ts_ms: i64) -> L2Snapshot {
        let half_spread = 50_000_000; // $0.5
        L2Snapshot {
            exch_ts: TsNanos::from_millis(ts_ms),
            recv_ts: TsNanos::from_millis(ts_ms),
            bids: vec![Level {
                px: Px::from_raw(mid_raw - half_spread),
                qty: "5".parse().unwrap(),
                n_orders: 1,
            }],
            asks: vec![Level {
                px: Px::from_raw(mid_raw + half_spread),
                qty: "5".parse().unwrap(),
                n_orders: 1,
            }],
        }
    }

    /// Feed a mid-price path; return the sequence of order intents.
    fn drive(mids: impl Iterator<Item = i64>) -> Vec<OrderIntent> {
        let mut strat = SmaCross::new(2, 4, "0.01".parse().unwrap());
        let mut book = OrderBook::new();
        let mut actions = Actions::default();
        let mut position = Qty::ZERO;
        let mut intents = Vec::new();
        for (i, mid) in mids.enumerate() {
            let snap = snapshot_at(mid, 1_000 + i as i64 * 500);
            let event = MarketEvent::L2Snapshot(snap.clone());
            book.apply_snapshot(&snap);
            strat.on_event(&event, &book, position, &mut actions);
            for intent in actions.take() {
                // assume full fills for this unit test
                position = match intent.side {
                    Side::Buy => position + intent.qty,
                    Side::Sell => position - intent.qty,
                };
                intents.push(intent);
            }
        }
        intents
    }

    #[test]
    fn rising_then_falling_path_flips_long_to_short() {
        let scale = 100_000_000i64;
        let up: Vec<i64> = (0..8).map(|i| (100_000 + i * 10) * scale).collect();
        let down: Vec<i64> = (0..8).map(|i| (100_070 - i * 10) * scale).collect();
        let intents = drive(up.into_iter().chain(down));

        assert!(!intents.is_empty(), "expected at least one signal");
        // First signal in a rising market is a buy to +0.01.
        assert_eq!(intents[0].side, Side::Buy);
        assert_eq!(intents[0].qty, "0.01".parse().unwrap());
        // A later flip sells 0.02 (close long, open short).
        let flip = intents.iter().find(|i| i.side == Side::Sell).expect("flip");
        assert_eq!(flip.qty, "0.02".parse().unwrap());
    }

    #[test]
    fn no_signals_before_slow_window_fills() {
        let scale = 100_000_000i64;
        let mids: Vec<i64> = (0..3).map(|i| (100_000 + i * 10) * scale).collect();
        assert!(drive(mids.into_iter()).is_empty());
    }
}
