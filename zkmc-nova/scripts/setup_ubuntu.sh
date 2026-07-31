#!/usr/bin/env bash
# Install Rust, Python, and pinned dependencies.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

sudo apt update
sudo apt install -y build-essential pkg-config libssl-dev clang cmake git curl python3-venv
if ! command -v rustup >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi
source "$HOME/.cargo/env"
rustup update
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
./scripts/setup_sonobe_offchain.sh
cargo generate-lockfile
rustc --version
cargo --version
