//! PyO3 bindings exposing the Rust core to Python as the `quantis_core` module.
//!
//! Design rule for this surface: it is *thin*. The bindings call the same
//! `quantis_backtest::runner` the CLI calls and hand back the artifact as a
//! JSON string, which the Python `quantis` package parses. Keeping the schema
//! single-sourced in serde (rather than rebuilt as a Python dict here) is what
//! prevents the binding from drifting from the artifact written to disk — and
//! is why a Python-driven backtest reproduces the CLI's determinism hash.
//!
//! Built with maturin (`crates/python/pyproject.toml`).

use std::path::PathBuf;

use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;

use quantis_backtest::runner::{RunError, run_from_config};
use quantis_market_data::recorder::EventReader;

/// Run a backtest from a TOML engine config and return the results artifact
/// as a JSON string (same content the CLI writes to `results/`).
#[pyfunction]
fn run_backtest_json(config_path: &str) -> PyResult<String> {
    let artifact = run_from_config(&PathBuf::from(config_path)).map_err(run_error_to_py)?;
    serde_json::to_string(&artifact)
        .map_err(|e| PyValueError::new_err(format!("serializing artifact: {e}")))
}

/// Read a recorded event log and return parallel arrays of the mid-price
/// series: `(exch_ts_ms, recv_ts_ms, mid_f64)` over L2 snapshots that have
/// both sides. Floats appear only here, at the research boundary; the engine
/// itself never sees them. Trades and one-sided books are skipped.
#[pyfunction]
fn read_mid_series(log_path: &str) -> PyResult<(Vec<i64>, Vec<i64>, Vec<f64>)> {
    use qcore::events::MarketEvent;
    use qcore::types::Px;

    let reader = EventReader::open(&PathBuf::from(log_path))
        .map_err(|e| PyIOError::new_err(format!("opening event log {log_path}: {e}")))?;

    let mut exch = Vec::new();
    let mut recv = Vec::new();
    let mut mids = Vec::new();
    for item in reader {
        let event = item.map_err(|e| PyIOError::new_err(format!("reading event log: {e}")))?;
        if let MarketEvent::L2Snapshot(snap) = event
            && let (Some(bid), Some(ask)) = (snap.bids.first(), snap.asks.first())
        {
            exch.push(snap.exch_ts.as_millis());
            recv.push(snap.recv_ts.as_millis());
            mids.push(Px::mid(bid.px, ask.px).to_f64());
        }
    }
    Ok((exch, recv, mids))
}

fn run_error_to_py(err: RunError) -> PyErr {
    match err {
        RunError::Config(_) | RunError::InstrumentMismatch { .. } => {
            PyValueError::new_err(err.to_string())
        }
        RunError::Io { .. } | RunError::Log(_) | RunError::TruncatedLog { .. } => {
            PyIOError::new_err(err.to_string())
        }
    }
}

#[pymodule]
fn quantis_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(run_backtest_json, m)?)?;
    m.add_function(wrap_pyfunction!(read_mid_series, m)?)?;
    Ok(())
}
