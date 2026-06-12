//! PyO3 bindings exposing the Rust core to Python as the `quantis_core` module.
//!
//! Built with maturin (`crates/python/pyproject.toml`). Phase 2 adds the
//! backtest runner and event-log readers; Phase 0 only proves the toolchain.

use pyo3::prelude::*;

#[pymodule]
fn quantis_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
