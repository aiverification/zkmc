#!/usr/bin/env python3
"""Confirm that standalone verification accepts valid and rejects modified artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable


def flip_byte(path: Path) -> None:
    """Flip one byte without changing file length."""
    data = bytearray(path.read_bytes())
    if not data:
        raise ValueError(f"cannot mutate empty file: {path}")
    index = len(data) // 2
    data[index] ^= 0x01
    path.write_bytes(data)


def mutate_json(path: Path, field: str, transform: Callable[[object], object]) -> None:
    """Change one top-level JSON field."""
    data = json.loads(path.read_text())
    data[field] = transform(data[field])
    path.write_text(json.dumps(data, indent=2) + "\n")


def run_verify(binary: Path, artifacts: Path, statement: Path, verifier: Path) -> subprocess.CompletedProcess[str]:
    """Run the public verifier without loading any private benchmark JSON."""
    return subprocess.run(
        [str(binary), "verify", str(artifacts), str(statement), str(verifier)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def main() -> None:
    """Exercise the valid path and independent rejection paths."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--statement", type=Path, required=True)
    parser.add_argument("--trusted-verifier", type=Path, required=True)
    args = parser.parse_args()

    valid = run_verify(args.binary, args.artifacts, args.statement, args.trusted_verifier)
    if valid.returncode != 0 or "VERIFICATION PASSED" not in valid.stdout:
        raise SystemExit(f"valid artifacts were rejected:\n{valid.stdout}")
    print("valid artifacts accepted")

    mutations: list[tuple[str, Callable[[Path, Path, Path], None]]] = [
        (
            "proof byte",
            lambda artifacts, _statement, _verifier: flip_byte(artifacts / "decider_proof.bin"),
        ),
        (
            "public-input byte",
            lambda artifacts, _statement, _verifier: flip_byte(artifacts / "decider_public.bin"),
        ),
        (
            "trusted verifier byte",
            lambda _artifacts, _statement, verifier: flip_byte(verifier),
        ),
        (
            "model commitment",
            lambda _artifacts, statement, _verifier: mutate_json(
                statement,
                "model_commitment",
                lambda value: str(int(str(value)) + 1),
            ),
        ),
        (
            "certificate commitment",
            lambda _artifacts, statement, _verifier: mutate_json(
                statement,
                "certificate_commitment",
                lambda value: str(int(str(value)) + 1),
            ),
        ),
        (
            "obligation count",
            lambda _artifacts, statement, _verifier: mutate_json(
                statement,
                "obligation_count",
                lambda value: int(value) + 1,
            ),
        ),
        (
            "range bound",
            lambda _artifacts, statement, _verifier: mutate_json(
                statement,
                "bound",
                lambda value: int(value) + 1,
            ),
        ),
        (
            "bundled statement",
            lambda artifacts, _statement, _verifier: mutate_json(
                artifacts / "statement.json",
                "benchmark",
                lambda value: str(value) + "-modified",
            ),
        ),
        (
            "manifest metadata",
            lambda artifacts, _statement, _verifier: mutate_json(
                artifacts / "manifest.json",
                "curve_cycle",
                lambda value: str(value) + "-modified",
            ),
        ),
        (
            "protocol version",
            lambda artifacts, _statement, _verifier: mutate_json(
                artifacts / "manifest.json",
                "protocol_version",
                lambda value: str(value) + "-modified",
            ),
        ),
        (
            "trailing proof bytes",
            lambda artifacts, _statement, _verifier: (
                artifacts / "decider_proof.bin"
            ).write_bytes((artifacts / "decider_proof.bin").read_bytes() + b"\x00"),
        ),
    ]

    with tempfile.TemporaryDirectory(prefix="zkmc-verifier-tests-") as temp_root:
        root = Path(temp_root)
        for index, (label, mutate) in enumerate(mutations):
            case_root = root / f"case-{index:02d}"
            artifacts = case_root / "proof"
            statement = case_root / "statement.json"
            verifier = case_root / "trusted-verifier.bin"
            shutil.copytree(args.artifacts, artifacts)
            case_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.statement, statement)
            shutil.copy2(args.trusted_verifier, verifier)
            mutate(artifacts, statement, verifier)
            result = run_verify(args.binary, artifacts, statement, verifier)
            if result.returncode == 0:
                raise SystemExit(f"modified {label} was incorrectly accepted:\n{result.stdout}")
            print(f"modified {label} rejected")

    print("FINAL VERIFICATION PHASE COMPLETE")


if __name__ == "__main__":
    main()
