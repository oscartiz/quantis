//! Fixed-point numeric types and timestamps used on the hot path.
//!
//! Prices, quantities, and cash are `i64` values scaled by 1e8
//! ([`FIXED_SCALE`]). Exchange feeds deliver decimal strings; parsing them
//! into integers once makes every downstream computation exact,
//! platform-independent, and hashable — which is what makes the seeded
//! results artifact bit-reproducible (see ADR-002). Floats exist only at the
//! research boundary, via explicit `to_f64`/`from_f64` conversions.

use std::fmt;
use std::ops::{Add, AddAssign, Neg, Sub, SubAssign};
use std::str::FromStr;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Scale factor: every fixed-point value carries 8 decimal places.
pub const FIXED_SCALE: i64 = 100_000_000;

/// Errors from parsing a decimal string into a fixed-point value.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum ParseFixedError {
    /// The input was empty or contained no digits.
    #[error("empty or digit-free input")]
    Empty,
    /// A character other than digits, a sign, or one decimal point appeared.
    #[error("invalid character in number")]
    InvalidDigit,
    /// More than 8 decimal places were supplied.
    #[error("more than 8 decimal places ({0})")]
    TooManyDecimals(usize),
    /// The value does not fit in the fixed-point range.
    #[error("value out of range")]
    Overflow,
}

fn parse_fixed8(s: &str) -> Result<i64, ParseFixedError> {
    let s = s.trim();
    let (neg, rest) = match s.strip_prefix('-') {
        Some(r) => (true, r),
        None => (false, s.strip_prefix('+').unwrap_or(s)),
    };
    let (int_part, frac_part) = match rest.split_once('.') {
        Some((i, f)) => (i, f),
        None => (rest, ""),
    };
    if int_part.is_empty() && frac_part.is_empty() {
        return Err(ParseFixedError::Empty);
    }
    if frac_part.len() > 8 {
        return Err(ParseFixedError::TooManyDecimals(frac_part.len()));
    }
    let mut int_val: i128 = 0;
    for b in int_part.bytes() {
        if !b.is_ascii_digit() {
            return Err(ParseFixedError::InvalidDigit);
        }
        int_val = int_val * 10 + i128::from(b - b'0');
        if int_val > i128::from(i64::MAX) {
            return Err(ParseFixedError::Overflow);
        }
    }
    let mut frac_val: i64 = 0;
    for b in frac_part.bytes() {
        if !b.is_ascii_digit() {
            return Err(ParseFixedError::InvalidDigit);
        }
        frac_val = frac_val * 10 + i64::from(b - b'0');
    }
    frac_val *= 10_i64.pow(8 - frac_part.len() as u32);
    let total = int_val * i128::from(FIXED_SCALE) + i128::from(frac_val);
    let total = if neg { -total } else { total };
    i64::try_from(total).map_err(|_| ParseFixedError::Overflow)
}

fn format_fixed8(v: i64, f: &mut fmt::Formatter<'_>) -> fmt::Result {
    let sign = if v < 0 { "-" } else { "" };
    let abs = v.unsigned_abs();
    let int = abs / FIXED_SCALE as u64;
    let frac = abs % FIXED_SCALE as u64;
    if frac == 0 {
        write!(f, "{sign}{int}")
    } else {
        let digits = format!("{frac:08}");
        write!(f, "{sign}{int}.{}", digits.trim_end_matches('0'))
    }
}

macro_rules! fixed_newtype {
    ($(#[$doc:meta])* $name:ident) => {
        $(#[$doc])*
        #[derive(
            Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default, Serialize, Deserialize,
        )]
        pub struct $name(i64);

        impl $name {
            /// The zero value.
            pub const ZERO: Self = Self(0);

            /// Construct from a raw 1e8-scaled integer.
            pub const fn from_raw(raw: i64) -> Self {
                Self(raw)
            }

            /// The raw 1e8-scaled integer.
            pub const fn raw(self) -> i64 {
                self.0
            }

            /// True if exactly zero.
            pub const fn is_zero(self) -> bool {
                self.0 == 0
            }

            /// Absolute value.
            pub const fn abs(self) -> Self {
                Self(self.0.abs())
            }

            /// Lossy conversion for the research boundary; never used on the
            /// hot path.
            pub fn to_f64(self) -> f64 {
                self.0 as f64 / FIXED_SCALE as f64
            }

            /// Lossy construction (rounds to 8 decimals); for tests and
            /// synthetic data only.
            pub fn from_f64(x: f64) -> Self {
                Self((x * FIXED_SCALE as f64).round() as i64)
            }
        }

        impl FromStr for $name {
            type Err = ParseFixedError;

            fn from_str(s: &str) -> Result<Self, Self::Err> {
                parse_fixed8(s).map(Self)
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                format_fixed8(self.0, f)
            }
        }

        impl fmt::Debug for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, concat!(stringify!($name), "("))?;
                format_fixed8(self.0, f)?;
                write!(f, ")")
            }
        }
    };
}

macro_rules! fixed_arith {
    ($name:ident) => {
        impl Add for $name {
            type Output = Self;
            fn add(self, rhs: Self) -> Self {
                Self(self.0 + rhs.0)
            }
        }
        impl Sub for $name {
            type Output = Self;
            fn sub(self, rhs: Self) -> Self {
                Self(self.0 - rhs.0)
            }
        }
        impl Neg for $name {
            type Output = Self;
            fn neg(self) -> Self {
                Self(-self.0)
            }
        }
        impl AddAssign for $name {
            fn add_assign(&mut self, rhs: Self) {
                self.0 += rhs.0;
            }
        }
        impl SubAssign for $name {
            fn sub_assign(&mut self, rhs: Self) {
                self.0 -= rhs.0;
            }
        }
    };
}

fixed_newtype!(
    /// A price in quote currency (USD for Hyperliquid perps), 1e8-scaled.
    Px
);
fixed_newtype!(
    /// A quantity in base units (e.g. BTC), 1e8-scaled. Signed: positive is
    /// long / buy, negative is short / sell.
    Qty
);
fixed_newtype!(
    /// An amount of quote currency (USD), 1e8-scaled. Signed.
    Cash
);

fixed_arith!(Qty);
fixed_arith!(Cash);

impl Px {
    /// Notional value of `qty` at this price, in quote currency. Sign follows
    /// `qty`. Panics on overflow (loudly, by design: a position whose
    /// notional overflows i64 at 1e8 scale is > $90 billion).
    pub fn notional(self, qty: Qty) -> Cash {
        let wide = i128::from(self.0) * i128::from(qty.0) / i128::from(FIXED_SCALE);
        Cash(i64::try_from(wide).expect("notional overflow"))
    }

    /// Integer midpoint of two prices (rounds toward negative infinity).
    pub fn mid(bid: Px, ask: Px) -> Px {
        Px((bid.0 + ask.0) / 2)
    }
}

// A price minus a price is a price-denominated delta (e.g. exit - entry), used
// for PnL. Only subtraction is defined; adding two absolute prices is
// meaningless and intentionally unavailable.
impl Sub for Px {
    type Output = Px;
    fn sub(self, rhs: Self) -> Self {
        Px(self.0 - rhs.0)
    }
}

impl Cash {
    /// Apply a fee rate expressed in parts-per-million to this (absolute)
    /// amount; result is always non-negative. Integer arithmetic throughout.
    pub fn fee_ppm(self, ppm: i64) -> Cash {
        let wide = i128::from(self.0.abs()) * i128::from(ppm) / 1_000_000;
        Cash(i64::try_from(wide).expect("fee overflow"))
    }

    /// Apply a signed rate in parts-per-million, preserving this amount's sign
    /// (and the rate's). Used for funding, where the cash flow direction
    /// depends on both position side and funding-rate sign.
    pub fn rate_ppm_signed(self, ppm: i64) -> Cash {
        let wide = i128::from(self.0) * i128::from(ppm) / 1_000_000;
        Cash(i64::try_from(wide).expect("funding overflow"))
    }
}

/// Which side of the market an order or aggressor is on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Side {
    /// Buying / bidding.
    Buy,
    /// Selling / asking.
    Sell,
}

impl Side {
    /// The opposing side.
    pub const fn opposite(self) -> Side {
        match self {
            Side::Buy => Side::Sell,
            Side::Sell => Side::Buy,
        }
    }

    /// +1 for buy, -1 for sell; used to sign quantities and cash flows.
    pub const fn sign(self) -> i64 {
        match self {
            Side::Buy => 1,
            Side::Sell => -1,
        }
    }

    /// Decode Hyperliquid's trade-side code: `"B"` = buy aggressor,
    /// `"A"` = sell aggressor.
    pub fn from_hl(code: &str) -> Option<Side> {
        match code {
            "B" => Some(Side::Buy),
            "A" => Some(Side::Sell),
            _ => None,
        }
    }
}

/// A point in time as nanoseconds since the Unix epoch.
///
/// Exchange timestamps arrive in milliseconds and are widened; receive
/// timestamps are taken locally at nanosecond resolution so feed latency
/// (`recv - exch`) can be measured. The two clocks are not synchronized —
/// latency figures include clock skew and say so wherever reported.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default, Serialize, Deserialize,
)]
pub struct TsNanos(i64);

impl TsNanos {
    /// From milliseconds since the Unix epoch (exchange convention).
    pub const fn from_millis(ms: i64) -> Self {
        Self(ms * 1_000_000)
    }

    /// From nanoseconds since the Unix epoch.
    pub const fn from_nanos(ns: i64) -> Self {
        Self(ns)
    }

    /// Nanoseconds since the Unix epoch.
    pub const fn as_nanos(self) -> i64 {
        self.0
    }

    /// Milliseconds since the Unix epoch (truncating).
    pub const fn as_millis(self) -> i64 {
        self.0 / 1_000_000
    }

    /// Current wall-clock time.
    pub fn now() -> Self {
        let dur = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock before Unix epoch");
        Self(i64::try_from(dur.as_nanos()).expect("timestamp overflow"))
    }

    /// Signed difference `self - earlier` in nanoseconds.
    pub const fn nanos_since(self, earlier: TsNanos) -> i64 {
        self.0 - earlier.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_typical_exchange_strings() {
        assert_eq!("104372.5".parse::<Px>().unwrap().raw(), 10_437_250_000_000);
        assert_eq!("0.0001".parse::<Qty>().unwrap().raw(), 10_000);
        assert_eq!("-3.25".parse::<Cash>().unwrap().raw(), -325_000_000);
        assert_eq!("42".parse::<Px>().unwrap().raw(), 42 * FIXED_SCALE);
        assert_eq!(".5".parse::<Px>().unwrap().raw(), FIXED_SCALE / 2);
    }

    #[test]
    fn display_roundtrips() {
        for s in ["104372.5", "0.0001", "-3.25", "42", "0"] {
            let v: Px = s.parse().unwrap();
            assert_eq!(v.to_string().parse::<Px>().unwrap(), v, "roundtrip {s}");
        }
        assert_eq!("104372.50".parse::<Px>().unwrap().to_string(), "104372.5");
    }

    #[test]
    fn rejects_garbage() {
        assert_eq!("".parse::<Px>(), Err(ParseFixedError::Empty));
        assert_eq!("-".parse::<Px>(), Err(ParseFixedError::Empty));
        assert_eq!("1.2.3".parse::<Px>(), Err(ParseFixedError::InvalidDigit));
        assert_eq!("abc".parse::<Px>(), Err(ParseFixedError::InvalidDigit));
        assert_eq!(
            "1.123456789".parse::<Px>(),
            Err(ParseFixedError::TooManyDecimals(9))
        );
        assert_eq!(
            "99999999999999999999".parse::<Px>(),
            Err(ParseFixedError::Overflow)
        );
    }

    #[test]
    fn notional_is_exact() {
        let px: Px = "100000".parse().unwrap();
        let qty: Qty = "0.5".parse().unwrap();
        assert_eq!(px.notional(qty), "50000".parse::<Cash>().unwrap());
        assert_eq!(px.notional(-qty), "-50000".parse::<Cash>().unwrap());
    }

    #[test]
    fn fee_ppm_is_integer_exact() {
        // 450 ppm (4.5 bps taker) on $50,000 = $22.50
        let notional: Cash = "50000".parse().unwrap();
        assert_eq!(notional.fee_ppm(450), "22.5".parse::<Cash>().unwrap());
        assert_eq!((-notional).fee_ppm(450), "22.5".parse::<Cash>().unwrap());
    }

    #[test]
    fn mid_truncates_deterministically() {
        let bid: Px = "100".parse().unwrap();
        let ask: Px = "100.00000001".parse().unwrap();
        assert_eq!(Px::mid(bid, ask), bid);
    }

    #[test]
    fn side_codes_and_signs() {
        assert_eq!(Side::from_hl("B"), Some(Side::Buy));
        assert_eq!(Side::from_hl("A"), Some(Side::Sell));
        assert_eq!(Side::from_hl("X"), None);
        assert_eq!(Side::Buy.opposite(), Side::Sell);
        assert_eq!(Side::Sell.sign(), -1);
    }

    #[test]
    fn timestamps_widen_and_diff() {
        let exch = TsNanos::from_millis(1_700_000_000_000);
        let recv = TsNanos::from_nanos(1_700_000_000_000_000_000 + 1_500_000);
        assert_eq!(recv.nanos_since(exch), 1_500_000);
        assert_eq!(exch.as_millis(), 1_700_000_000_000);
    }
}
