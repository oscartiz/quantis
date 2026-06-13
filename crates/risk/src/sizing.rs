//! Position sizing: volatility targeting and capped fractional Kelly.
//!
//! Both return a *proposal*; the [`crate::gate`] independently enforces hard
//! limits, so an over-aggressive sizing estimate cannot by itself create an
//! oversized position.

use quantis_core::types::{Cash, Px, Qty};

/// Size a position so its expected volatility matches a target.
///
/// We want the position notional `N` such that `N * realized_vol ≈
/// equity * target_vol`, i.e. `N = equity * target_vol / realized_vol`, then
/// `qty = N / price`. Notional is capped at `max_leverage * equity` so a tiny
/// `realized_vol` cannot demand an absurd position.
///
/// `realized_vol` and `target_vol` must be in the same units (e.g. both daily,
/// or both annualized). Returns a non-negative quantity (direction is the
/// caller's concern). A non-positive `realized_vol`, `equity`, or `price`
/// yields zero — we never divide by zero or size off nonsense.
pub fn vol_target_qty(
    equity: Cash,
    price: Px,
    realized_vol: f64,
    target_vol: f64,
    max_leverage: f64,
) -> Qty {
    if realized_vol <= 0.0
        || target_vol <= 0.0
        || max_leverage <= 0.0
        || equity.raw() <= 0
        || price.raw() <= 0
        || !realized_vol.is_finite()
        || !target_vol.is_finite()
    {
        return Qty::ZERO;
    }
    let equity_f = equity.to_f64();
    let mut notional = equity_f * (target_vol / realized_vol);
    let cap = equity_f * max_leverage;
    if notional > cap {
        notional = cap;
    }
    let qty = notional / price.to_f64();
    Qty::from_f64(qty.max(0.0))
}

/// Capped fractional Kelly fraction of equity to allocate.
///
/// For a Gaussian bet the full-Kelly fraction is `edge / variance` (expected
/// return over variance of return). We scale it by `fraction` (e.g. 0.25 for
/// quarter-Kelly — full Kelly is famously too aggressive under parameter
/// uncertainty) and clamp the magnitude to `cap`. The sign is preserved, so a
/// negative edge proposes a short.
///
/// `variance <= 0`, or non-finite inputs, yield zero.
pub fn capped_fractional_kelly(edge: f64, variance: f64, fraction: f64, cap: f64) -> f64 {
    if variance <= 0.0
        || fraction < 0.0
        || cap < 0.0
        || !edge.is_finite()
        || !variance.is_finite()
        || !fraction.is_finite()
        || !cap.is_finite()
    {
        return 0.0;
    }
    let full_kelly = edge / variance;
    let scaled = full_kelly * fraction;
    scaled.clamp(-cap, cap)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cash(s: &str) -> Cash {
        s.parse().unwrap()
    }
    fn px(s: &str) -> Px {
        s.parse().unwrap()
    }

    #[test]
    fn vol_target_scales_inversely_with_realized_vol() {
        // Halving realized vol should roughly double the position (until capped).
        let a = vol_target_qty(cash("100000"), px("100000"), 0.40, 0.20, 10.0);
        let b = vol_target_qty(cash("100000"), px("100000"), 0.20, 0.20, 10.0);
        assert!(b.to_f64() > a.to_f64() * 1.9);
        assert!(b.to_f64() < a.to_f64() * 2.1);
    }

    #[test]
    fn vol_target_respects_leverage_cap() {
        // realized_vol tiny -> target wants a huge position, but cap binds.
        let q = vol_target_qty(cash("100000"), px("100000"), 0.0001, 0.20, 3.0);
        // max notional = 3 * 100000 = 300000; at price 100000 -> 3.0 qty
        assert!((q.to_f64() - 3.0).abs() < 1e-6);
    }

    #[test]
    fn vol_target_is_zero_on_degenerate_inputs() {
        assert_eq!(
            vol_target_qty(cash("100000"), px("100000"), 0.0, 0.2, 10.0),
            Qty::ZERO
        );
        assert_eq!(
            vol_target_qty(cash("0"), px("100000"), 0.2, 0.2, 10.0),
            Qty::ZERO
        );
        assert_eq!(
            vol_target_qty(cash("100000"), px("100000"), f64::NAN, 0.2, 10.0),
            Qty::ZERO
        );
    }

    #[test]
    fn quarter_kelly_is_quarter_of_full() {
        // full kelly = edge/var = 0.10/0.04 = 2.5; quarter = 0.625; under cap 1.0
        let f = capped_fractional_kelly(0.10, 0.04, 0.25, 1.0);
        assert!((f - 0.625).abs() < 1e-12);
    }

    #[test]
    fn kelly_cap_binds_and_preserves_sign() {
        assert_eq!(capped_fractional_kelly(1.0, 0.01, 1.0, 0.5), 0.5);
        assert_eq!(capped_fractional_kelly(-1.0, 0.01, 1.0, 0.5), -0.5);
    }

    #[test]
    fn kelly_zero_on_degenerate_inputs() {
        assert_eq!(capped_fractional_kelly(0.1, 0.0, 0.25, 1.0), 0.0);
        assert_eq!(capped_fractional_kelly(f64::INFINITY, 0.04, 0.25, 1.0), 0.0);
    }
}
