#!/usr/bin/env bash
# Enforce focused hand-written source file sizes.
set -euo pipefail

while IFS= read -r file; do
  lines=$(wc -l < "$file")
  if (( lines > 500 )); then
    echo "$file exceeds 500 lines: $lines" >&2
    exit 1
  fi
done < <(find src scripts -type f \( -name '*.rs' -o -name '*.py' -o -name '*.sh' \) | sort)

echo "source structure check passed"
