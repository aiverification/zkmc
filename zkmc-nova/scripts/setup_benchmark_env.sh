#!/usr/bin/env bash
# Prepare pinned dependencies and the official ZKMC encoder once.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM="$ROOT/.vendor/zkmc-upstream"
UPSTREAM_COMMIT="112b470337cbe13c8b1aa21dc9bd199eb6ce5a40"

cd "$ROOT"
source "$HOME/.cargo/env"
command -v /usr/bin/time >/dev/null 2>&1 || {
  echo "missing /usr/bin/time; install the Ubuntu 'time' package" >&2
  exit 1
}

./scripts/setup_sonobe_offchain.sh
mkdir -p "$ROOT/.vendor" "$ROOT/artifacts/benchmarks"

if [[ ! -d "$UPSTREAM/.git" ]]; then
  git clone --filter=blob:none --no-checkout \
    https://github.com/aiverification/zkmc.git "$UPSTREAM"
fi
git -C "$UPSTREAM" fetch --depth 1 origin "$UPSTREAM_COMMIT"
git -C "$UPSTREAM" checkout --detach "$UPSTREAM_COMMIT"
test "$(git -C "$UPSTREAM" rev-parse HEAD)" = "$UPSTREAM_COMMIT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -e "$UPSTREAM/encoder"

cargo metadata --locked --format-version 1 >/dev/null
printf 'benchmark environment ready\n'
