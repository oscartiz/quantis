//! The paper gateway: simulated fills against live (or replayed) market data,
//! using the **same** matching engine and risk gate as everything else.
//!
//! This is the point of the whole two-language design: `quantis_backtest`'s
//! [`FillEngine`] matches paper orders exactly as it matches backtest orders,
//! and `quantis_risk`'s [`RiskGate`] vets paper orders exactly as it would vet
//! live ones. A backtest and a paper run can therefore only diverge through
//! *data* and *timing* — never through different fill or risk logic — which is
//! what makes the Phase 4 backtest-vs-paper gap report meaningful.
//!
//! Timing note (a known, documented gap source): the backtester delays
//! execution to the next snapshot (ADR-004), whereas the paper gateway fills
//! against the latest book at submission. The gap report attributes the
//! resulting difference rather than hiding it.

use quantis_backtest::fill::{FillEngine, FillParams};
use quantis_core::events::MarketEvent;
use quantis_core::types::{Cash, Px, Qty, TsNanos};
use quantis_market_data::book::OrderBook;
use quantis_risk::{RiskDecision, RiskGate};
use tracing::{debug, info};

use crate::gateway::{GatewayError, OrderGateway};
use crate::manager::OrderManager;
use crate::order::{ClientOrderId, ExecReport, OrderKind, OrderRequest};

/// A paper-trading venue.
pub struct PaperGateway {
    book: OrderBook,
    fill_engine: FillEngine,
    risk: RiskGate,
    manager: OrderManager,
    initial_cash: Cash,
    pending_reports: Vec<ExecReport>,
    next_fill_id: u64,
    last_ts: TsNanos,
}

impl PaperGateway {
    /// Build a paper gateway with a fee schedule, risk gate, and starting cash.
    pub fn new(fill: FillParams, risk: RiskGate, initial_cash: Cash) -> Self {
        Self {
            book: OrderBook::new(),
            fill_engine: FillEngine::new(fill),
            risk,
            manager: OrderManager::new(),
            initial_cash,
            pending_reports: Vec::new(),
            next_fill_id: 0,
            last_ts: TsNanos::from_millis(0),
        }
    }

    /// Feed a market-data event. Updates the book and the risk gate's equity
    /// mark (so drawdown limits track in real time).
    pub fn on_event(&mut self, event: &MarketEvent) {
        self.last_ts = event.exch_ts();
        if let MarketEvent::L2Snapshot(snap) = event {
            self.book.apply_snapshot(snap);
            self.risk.on_equity(self.equity());
        }
    }

    /// Current mark-to-mid equity: starting cash + realized PnL + unrealized.
    pub fn equity(&self) -> Cash {
        let unrealized = match self.book.mid() {
            Some(mid) => {
                let pos = self.manager.position();
                if pos.raw() == 0 {
                    Cash::ZERO
                } else {
                    let entry = self.manager.avg_entry();
                    let per = if pos.raw() > 0 {
                        mid - entry
                    } else {
                        entry - mid
                    };
                    per.notional(pos.abs())
                }
            }
            None => Cash::ZERO,
        };
        self.initial_cash + self.manager.realized_pnl() + unrealized
    }

    /// The order manager (position, realized PnL, fills).
    pub fn manager(&self) -> &OrderManager {
        &self.manager
    }

    /// Whether the risk kill switch is tripped.
    pub fn is_killed(&self) -> bool {
        self.risk.is_killed()
    }

    fn mark_price(&self) -> Option<Px> {
        self.book.mid()
    }

    fn emit(&mut self, report: ExecReport) {
        self.manager.apply(&report);
        self.pending_reports.push(report);
    }
}

impl OrderGateway for PaperGateway {
    fn submit(&mut self, request: OrderRequest) -> Result<(), GatewayError> {
        if !self.manager.register(request) {
            return Err(GatewayError::DuplicateCloid(request.cloid));
        }
        let Some(mark) = self.mark_price() else {
            // No book yet: reject rather than guess a price.
            self.emit(ExecReport::Rejected {
                cloid: request.cloid,
                reason: "no market data yet".into(),
            });
            return Ok(());
        };

        // Pre-trade risk check — the same gate the live path uses.
        if let RiskDecision::Veto(reason) =
            self.risk
                .check_order(self.manager.position(), request.side, request.qty, mark)
        {
            let reason = format!("{reason:?}");
            debug!(cloid = %request.cloid, %reason, "paper order vetoed by risk gate");
            self.emit(ExecReport::Rejected {
                cloid: request.cloid,
                reason,
            });
            return Ok(());
        }

        self.emit(ExecReport::Ack {
            cloid: request.cloid,
        });

        // Market orders fill against the current book via the shared engine.
        if let OrderKind::Market = request.kind {
            let outcome =
                self.fill_engine
                    .market(request.side, request.qty, &self.book, self.last_ts);
            for fill in outcome.fills {
                let fill_id = self.next_fill_id;
                self.next_fill_id += 1;
                self.emit(ExecReport::Fill {
                    cloid: request.cloid,
                    fill_id,
                    qty: fill.qty,
                    px: fill.px,
                    fee: fill.fee,
                });
            }
            if outcome.unfilled.raw() > 0 {
                info!(
                    cloid = %request.cloid,
                    unfilled = %outcome.unfilled,
                    "paper order partially filled: insufficient visible depth"
                );
            }
        }
        // Limit orders rest until a future fill model matches them (ADR-004).
        Ok(())
    }

    fn cancel(&mut self, cloid: ClientOrderId) -> Result<(), GatewayError> {
        match self.manager.order(cloid) {
            Some(o) if !o.status.is_terminal() => {
                self.emit(ExecReport::Cancelled { cloid });
                Ok(())
            }
            Some(_) => Ok(()), // already terminal; cancel is a no-op
            None => Err(GatewayError::Venue(format!("unknown order {cloid}"))),
        }
    }

    fn poll_reports(&mut self) -> Vec<ExecReport> {
        std::mem::take(&mut self.pending_reports)
    }

    fn position(&self) -> Qty {
        self.manager.position()
    }

    fn venue_name(&self) -> &'static str {
        "paper"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use quantis_core::events::{L2Snapshot, Level};
    use quantis_core::types::Side;
    use quantis_risk::RiskLimits;

    fn fill_params() -> FillParams {
        FillParams {
            taker_fee_ppm: 450,
            maker_fee_ppm: 150,
        }
    }

    fn limits() -> RiskLimits {
        RiskLimits {
            max_position_qty: "1.0".parse().unwrap(),
            max_notional: "150000".parse().unwrap(),
            max_drawdown_frac: 0.20,
        }
    }

    fn snapshot(bid: &str, ask: &str, sz: &str) -> MarketEvent {
        MarketEvent::L2Snapshot(L2Snapshot {
            exch_ts: TsNanos::from_millis(1_000),
            recv_ts: TsNanos::from_millis(1_001),
            bids: vec![Level {
                px: bid.parse().unwrap(),
                qty: sz.parse().unwrap(),
                n_orders: 1,
            }],
            asks: vec![Level {
                px: ask.parse().unwrap(),
                qty: sz.parse().unwrap(),
                n_orders: 1,
            }],
        })
    }

    fn gateway() -> PaperGateway {
        let risk = RiskGate::new(limits(), "100000".parse().unwrap()).unwrap();
        PaperGateway::new(fill_params(), risk, "100000".parse().unwrap())
    }

    fn market(cloid: u128, side: Side, q: &str) -> OrderRequest {
        OrderRequest {
            cloid: ClientOrderId(cloid),
            side,
            qty: q.parse().unwrap(),
            kind: OrderKind::Market,
            reduce_only: false,
        }
    }

    #[test]
    fn fills_a_market_order_against_the_book() {
        let mut g = gateway();
        g.on_event(&snapshot("99999", "100001", "5"));
        g.submit(market(1, Side::Buy, "0.5")).unwrap();
        let reports = g.poll_reports();
        assert!(reports.iter().any(|r| matches!(r, ExecReport::Ack { .. })));
        assert!(reports.iter().any(|r| matches!(r, ExecReport::Fill { .. })));
        assert_eq!(g.position(), "0.5".parse::<Qty>().unwrap());
    }

    #[test]
    fn risk_gate_vetoes_oversized_order() {
        let mut g = gateway();
        g.on_event(&snapshot("99999", "100001", "5"));
        // 2.0 BTC > 1.0 position cap -> rejected
        g.submit(market(1, Side::Buy, "2.0")).unwrap();
        let reports = g.poll_reports();
        assert!(
            reports
                .iter()
                .any(|r| matches!(r, ExecReport::Rejected { .. }))
        );
        assert_eq!(g.position(), Qty::ZERO);
    }

    #[test]
    fn rejects_before_any_market_data() {
        let mut g = gateway();
        g.submit(market(1, Side::Buy, "0.5")).unwrap();
        let reports = g.poll_reports();
        assert!(matches!(reports.as_slice(), [ExecReport::Rejected { .. }]));
    }

    #[test]
    fn duplicate_cloid_is_rejected() {
        let mut g = gateway();
        g.on_event(&snapshot("99999", "100001", "5"));
        g.submit(market(1, Side::Buy, "0.1")).unwrap();
        let err = g.submit(market(1, Side::Buy, "0.1")).unwrap_err();
        assert!(matches!(err, GatewayError::DuplicateCloid(_)));
    }
}
