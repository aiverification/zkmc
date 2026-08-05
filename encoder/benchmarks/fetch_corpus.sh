#!/usr/bin/env bash
# Fetch KoAT's Complexity_ITS benchmark corpus (838 .koat files) from TermComp's TPDB into a
# gitignored local directory. One-time download; the corpus is NOT committed (too large).
#
# Usage:  bash benchmarks/fetch_corpus.sh
# Then:   uv run python benchmarks/run_its.py --corpus benchmarks/corpus/Complexity_ITS

set -euo pipefail

DEST="$(cd "$(dirname "$0")" && pwd)/corpus"
# Pinned TPDB revision so REPORT.md numbers stay reproducible against a fixed corpus.
TPDB_REV="bf19a3c906ac76a49251779e41c3adf144caa16c"
EXPECTED=838

count() { find "$DEST/Complexity_ITS" -name '*.koat' 2>/dev/null | wc -l | tr -d ' '; }

if [ -d "$DEST/Complexity_ITS" ] && [ "$(count)" -eq "$EXPECTED" ]; then
    echo "Corpus already present at $DEST/Complexity_ITS ($(count) .koat files)."
    exit 0
fi

# Missing or partial (e.g. an interrupted earlier checkout): start clean.
echo "Sparse-cloning TermCOMP/TPDB Complexity_ITS into $DEST ..."
rm -rf "$DEST"
git clone --filter=blob:none --sparse https://github.com/TermCOMP/TPDB.git "$DEST"
git -C "$DEST" sparse-checkout set Complexity_ITS
git -C "$DEST" checkout --quiet "$TPDB_REV"

if [ "$(count)" -ne "$EXPECTED" ]; then
    echo "Warning: expected $EXPECTED .koat files, found $(count). TPDB layout may have changed." >&2
fi
echo "Done: $(count) .koat files under $DEST/Complexity_ITS"
