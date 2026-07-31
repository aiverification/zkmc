#!/usr/bin/env bash
# Prepare the pinned offchain Sonobe dependency.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SONOBE="$ROOT/.vendor/sonobe"
COMMIT="9b7dd34f0e0341046baeabc6f900f5ee63007f18"
DECIDER="$SONOBE/folding-schemes/src/folding/nova/decider.rs"

mkdir -p "$ROOT/.vendor"
if [[ ! -d "$SONOBE/.git" ]]; then
  git clone --filter=blob:none --no-checkout \
    https://github.com/privacy-ethereum/sonobe.git "$SONOBE"
fi

git -C "$SONOBE" fetch --depth 1 origin "$COMMIT"
git -C "$SONOBE" reset --hard "$COMMIT"
test "$(git -C "$SONOBE" rev-parse HEAD)" = "$COMMIT"

python3 - "$DECIDER" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = "#[derive(Debug, Clone, Eq, PartialEq)]\npub struct Proof"
new = (
    "#[derive(Debug, Clone, Eq, PartialEq, "
    "CanonicalSerialize, CanonicalDeserialize)]\n"
    "pub struct Proof"
)
if new not in text:
    if old not in text:
        raise SystemExit("unexpected Sonobe offchain proof declaration")
    path.write_text(text.replace(old, new, 1))
PY

grep -q "CanonicalSerialize, CanonicalDeserialize" "$DECIDER"
echo "prepared Sonobe offchain decider at $COMMIT"
