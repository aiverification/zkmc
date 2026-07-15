#!/usr/bin/env bash
# Run formatting, tests, and smoke checks.
set -euo pipefail
source "$HOME/.cargo/env"
./scripts/check_structure.sh
.venv/bin/python scripts/solve_farkas.py \
  examples/normalized_template.json /tmp/zkmc-obligations.json
cargo fmt --check
cargo test
cargo run --release -- plain /tmp/zkmc-obligations.json
cargo run --release -- circuit /tmp/zkmc-obligations.json
