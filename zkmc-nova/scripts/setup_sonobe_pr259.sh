#!/usr/bin/env bash
# Prepare pinned Sonobe PR 259.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SONOBE="$ROOT/.vendor/sonobe-259"
PATCH="$ROOT/patches/sonobe-pr259-serialization.patch"
COMMIT="243391ebc14ad993f425802eb9dbaf44fdd54436"
FILES=(
  crates/fs/src/nova/instances/mod.rs
  crates/primitives/src/commitments/mod.rs
  crates/snarks/src/cp/legogroth16/mod.rs
)

mkdir -p "$ROOT/.vendor"
if [[ ! -d "$SONOBE/.git" ]]; then
  git clone --filter=blob:none \
    https://github.com/privacy-ethereum/sonobe.git "$SONOBE"
fi

if [[ "$(git -C "$SONOBE" rev-parse HEAD 2>/dev/null || true)" = "$COMMIT" ]] \
  && git -C "$SONOBE" diff -- "${FILES[@]}" | cmp -s - "$PATCH" \
  && [[ -z "$(git -C "$SONOBE" status --porcelain --untracked-files=all | grep -vE '^ M (crates/fs/src/nova/instances/mod.rs|crates/primitives/src/commitments/mod.rs|crates/snarks/src/cp/legogroth16/mod.rs)$' || true)" ]]; then
  echo "prepared Sonobe PR 259 at $COMMIT"
  exit 0
fi

if [[ -n "$(git -C "$SONOBE" status --porcelain --untracked-files=all)" ]]; then
  echo "refusing to replace unexpected Sonobe changes" >&2
  exit 1
fi

git -C "$SONOBE" fetch --depth 1 origin "$COMMIT"
git -C "$SONOBE" checkout --detach "$COMMIT"
test "$(git -C "$SONOBE" rev-parse HEAD)" = "$COMMIT"
git -C "$SONOBE" apply --check "$PATCH"
git -C "$SONOBE" apply "$PATCH"
git -C "$SONOBE" diff -- "${FILES[@]}" | cmp -s - "$PATCH"
echo "prepared Sonobe PR 259 at $COMMIT"
