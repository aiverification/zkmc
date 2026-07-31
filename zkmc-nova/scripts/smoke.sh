#!/usr/bin/env bash
# Run the complete three-obligation proof flow.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INPUT="/tmp/zkmc-obligations.json"
OUTPUT="/tmp/zkmc-phase3-smoke"
STATEMENT="/tmp/zkmc-phase3-statement.json"

cd "$ROOT"
source "$HOME/.cargo/env"
./scripts/setup_sonobe_offchain.sh
cargo metadata --locked --format-version 1 >/dev/null 2>&1 || cargo generate-lockfile
./scripts/check_structure.sh
.venv/bin/python scripts/solve_farkas.py \
  examples/normalized_template.json "$INPUT"
cargo fmt --check
cargo test --locked -- --nocapture
rm -rf "$OUTPUT"
rm -f "$STATEMENT"
cargo run --release --locked -- commit "$INPUT" "$STATEMENT"
cargo run --release --locked -- all "$INPUT" "$OUTPUT" "$STATEMENT"
test -s "$OUTPUT/statement.json"
test -s "$OUTPUT/decider_proof.bin"
test -s "$OUTPUT/decider_verifier.bin"
test -s "$OUTPUT/decider_public.bin"
test -s "$OUTPUT/manifest.json"
grep -q '"decider": "Sonobe Nova offchain decider"' "$OUTPUT/manifest.json"
