#!/usr/bin/env bash
# Reproduce and fold the official 217 obligations.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM="$ROOT/.vendor/zkmc-upstream"
UPSTREAM_COMMIT="112b470337cbe13c8b1aa21dc9bd199eb6ce5a40"
OFFICIAL_JSON="$ROOT/artifacts/exb_i1a2_official.json"
BATCH_JSON="$ROOT/examples/exb_i1a2.json"
LOG="$ROOT/artifacts/exb_i1a2_phase2.log"

cd "$ROOT"
source "$HOME/.cargo/env"
mkdir -p "$ROOT/.vendor" "$ROOT/artifacts"

if [[ ! -d "$UPSTREAM/.git" ]]; then
  git clone --filter=blob:none --no-checkout https://github.com/aiverification/zkmc.git "$UPSTREAM"
fi
git -C "$UPSTREAM" fetch --depth 1 origin "$UPSTREAM_COMMIT"
git -C "$UPSTREAM" checkout --detach "$UPSTREAM_COMMIT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -e "$UPSTREAM/encoder"

"$ROOT/.venv/bin/zkfarkas" --pretty \
  "$UPSTREAM/encoder/examples/exp_backoff_guard_opt.gc" \
  --const initialDelay=1 \
  --const maxAttempts=2 \
  > "$OFFICIAL_JSON"

"$ROOT/.venv/bin/python" scripts/adapt_official.py \
  "$OFFICIAL_JSON" "$BATCH_JSON" src/generated_config.rs

./scripts/check_structure.sh
cargo fmt --check
cargo test -- --nocapture

: > "$LOG"
cargo run --release -- inspect "$BATCH_JSON" | tee -a "$LOG"
cargo run --release -- plain "$BATCH_JSON" | tee -a "$LOG"
cargo run --release -- circuit "$BATCH_JSON" | tee -a "$LOG"
RUST_BACKTRACE=1 cargo run --release -- nova "$BATCH_JSON" | tee -a "$LOG"

grep -q "obligations=217" "$LOG"
grep -q "plain checks passed: 217 obligations" "$LOG"
grep -q "circuit checks passed: 217 obligations" "$LOG"
grep -q "ivc verification passed" "$LOG"

echo "PHASE 2 COMPLETE: 217 official exb_i1a2 obligations folded and verified."
echo "Log: $LOG"
