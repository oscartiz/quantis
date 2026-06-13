//! Small statistics helpers for latency and throughput reporting.

/// Order statistics over a set of i64 samples (typically nanoseconds).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Percentiles {
    /// Number of samples.
    pub count: usize,
    /// Median.
    pub p50: i64,
    /// 95th percentile.
    pub p95: i64,
    /// 99th percentile.
    pub p99: i64,
    /// Maximum.
    pub max: i64,
}

/// Compute percentiles by nearest-rank on a sorted copy; `None` if empty.
pub fn percentiles(mut samples: Vec<i64>) -> Option<Percentiles> {
    if samples.is_empty() {
        return None;
    }
    samples.sort_unstable();
    // Nearest-rank: index ceil(p*N) - 1, clamped to the valid range.
    let pick = |p: f64| {
        let idx = ((samples.len() as f64 * p).ceil() as usize)
            .saturating_sub(1)
            .min(samples.len() - 1);
        samples[idx]
    };
    Some(Percentiles {
        count: samples.len(),
        p50: pick(0.50),
        p95: pick(0.95),
        p99: pick(0.99),
        max: *samples.last().expect("non-empty"),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_is_none() {
        assert!(percentiles(vec![]).is_none());
    }

    #[test]
    fn known_distribution() {
        let p = percentiles((1..=100).collect()).unwrap();
        assert_eq!(p.count, 100);
        assert_eq!(p.p50, 50);
        assert_eq!(p.p95, 95);
        assert_eq!(p.p99, 99);
        assert_eq!(p.max, 100);
    }
}
