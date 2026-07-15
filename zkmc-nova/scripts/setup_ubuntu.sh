#!/usr/bin/env bash
# Install Rust and Phase Two dependencies.
set -euo pipefail
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
rustc --version
cargo --version
