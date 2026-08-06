#!/usr/bin/env bash
# Forward legacy Sonobe setup calls.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/scripts/setup_sonobe_pr259.sh"
