#!/usr/bin/env bash
# Fetch KoAT's Complexity_ITS benchmark corpus (838 .koat files) from TermComp's TPDB into a
# gitignored local directory. One-time download; the corpus is NOT committed (too large).
#
# Usage:  bash benchmarks/fetch_corpus.sh
# Then:   uv run python benchmarks/run_its.py --corpus benchmarks/corpus/Complexity_ITS

set -euo pipefail

DEST="$(cd "$(dirname "$0")" && pwd)/corpus"

if [ -d "$DEST/Complexity_ITS" ]; then
    echo "Corpus already present at $DEST/Complexity_ITS ($(find "$DEST/Complexity_ITS" -name '*.koat' | wc -l | tr -d ' ') .koat files)."
    exit 0
fi

echo "Sparse-cloning TermCOMP/TPDB Complexity_ITS into $DEST ..."
rm -rf "$DEST"
git clone --depth 1 --filter=blob:none --sparse https://github.com/TermCOMP/TPDB.git "$DEST"
git -C "$DEST" sparse-checkout set Complexity_ITS

echo "Done: $(find "$DEST/Complexity_ITS" -name '*.koat' | wc -l | tr -d ' ') .koat files under $DEST/Complexity_ITS"
