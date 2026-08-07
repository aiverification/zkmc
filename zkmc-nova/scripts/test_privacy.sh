#!/usr/bin/env bash
# Test hiding Nova proof randomness.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
bash ./scripts/setup_sonobe_pr259.sh
cargo test --release --locked nova_pipeline_uses_hiding_randomness -- --ignored --nocapture
