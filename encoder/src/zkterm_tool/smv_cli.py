"""CLI for parsing a NuSMV/SMV file and printing the parsed SMV AST summary."""

from __future__ import annotations

import argparse
import sys

from .smv_parser import parse_smv
from .smv_types import SmvModel


def format_smv_model(model: SmvModel) -> str:
    lines: list[str] = [f"MODULE {model.module}"]

    if model.variables:
        lines.append("")
        lines.append("VAR")
        for var in model.variables:
            lines.append(f"  {var};")

    if model.defines:
        lines.append("")
        lines.append("DEFINE")
        for define in model.defines:
            lines.append(f"  {define};")

    if model.assignments:
        lines.append("")
        lines.append("ASSIGN")
        for assignment in model.assignments:
            lines.append(f"  {assignment};")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse a NuSMV/SMV file and print the parsed model summary.",
        epilog="""
Example:
  zksmv model.smv

This command only parses SMV. SMV-to-GC lowering is intentionally not wired in yet.
        """,
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="Input .smv file (default: stdin)",
    )
    args = parser.parse_args(argv)

    try:
        text = args.file.read()
        if not text.strip():
            print("Error: empty input", file=sys.stderr)
            return 1
        model = parse_smv(text)
        print(format_smv_model(model))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
