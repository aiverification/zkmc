#!/usr/bin/env bash
# Run the complete three-obligation proof flow.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INPUT="/tmp/zkmc-obligations.json"
OUTPUT="/tmp/zkmc-phase3-smoke"
STATEMENT="/tmp/zkmc-phase3-statement.json"

cd "$ROOT"
source "$HOME/.cargo/env"
./scripts/setup_sonobe_pr259.sh
cargo metadata --locked --format-version 1 >/dev/null
./scripts/check_structure.sh
.venv/bin/python scripts/solve_farkas.py \
  examples/normalized_template.json "$INPUT"
rustfmt --edition 2024 --check src/commitment.rs src/model.rs src/input.rs src/circuit/input.rs src/circuit/constraints.rs src/decider.rs src/runner.rs src/tests.rs
cargo test --locked -- --nocapture
./scripts/test_privacy.sh
rm -rf "$OUTPUT"
rm -f "$STATEMENT"
cargo run --release --locked -- commit "$INPUT" "$STATEMENT"
cargo run --release --locked -- all "$INPUT" "$OUTPUT" "$STATEMENT"
test -s "$OUTPUT/statement.json"
test -s "$OUTPUT/decider_proof.bin"
test -s "$OUTPUT/decider_verifier.bin"
test -s "$OUTPUT/decider_public.bin"
test -s "$OUTPUT/manifest.json"
grep -q '"decider": "Sonobe CycleFold LegoGroth16 decider"' "$OUTPUT/manifest.json"
grep -q '"protocol_version": "zkmc-nova-pedersen-legogroth16-v1"' "$OUTPUT/manifest.json"
.venv/bin/python scripts/test_verification.py \
  --binary target/release/zkmc \
  --artifacts "$OUTPUT" \
  --statement "$STATEMENT" \
  --trusted-verifier "$OUTPUT/decider_verifier.bin"
