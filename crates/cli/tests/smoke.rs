//! End-to-end smoke test: the seeded backtest over the committed sample data
//! must reproduce the golden determinism hash. This is the same check `make
//! smoke` and CI run, but pulled into `cargo test` so it also guards local
//! runs and the `rust` CI job. Build profile is irrelevant — the hashed
//! section is pure integer arithmetic.

use std::path::PathBuf;
use std::process::Command;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("repo root resolves")
}

#[test]
fn seeded_backtest_reproduces_golden_hash() {
    let root = repo_root();
    let expected = std::fs::read_to_string(root.join("tests/smoke/expected_hash.txt"))
        .expect("golden hash file exists");
    let expected = expected.trim();

    let status = Command::new(env!("CARGO_BIN_EXE_quantis"))
        .current_dir(&root)
        .args([
            "backtest",
            "--config",
            "config/engine.example.toml",
            "--expect-hash",
            expected,
        ])
        .status()
        .expect("quantis binary runs");

    assert!(
        status.success(),
        "seeded backtest did not reproduce the golden hash {expected}; \
         if a change to fill logic, fixed-point math, config, or sample data \
         was intentional, update tests/smoke/expected_hash.txt in the same \
         commit and explain why"
    );
}
