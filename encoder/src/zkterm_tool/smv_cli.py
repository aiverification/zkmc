"""CLI for importing a NuSMV/SMV file and printing the derived guarded commands."""

from __future__ import annotations

import argparse
import sys

from .smv_parser import parse_smv
from .smv_to_gc import smv_to_gc
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


def format_gc_result_from_smv(model: SmvModel) -> str:
    result = smv_to_gc(model)
    lines: list[str] = [f"// SMV module {model.module} -> guarded commands"]

    if result.types:
        for type_def in result.types.values():
            lines.append(str(type_def))

    init_str = " && ".join(str(c) for c in result.init_condition) if result.init_condition else "true"
    lines.append(f"init: {init_str}")
    lines.append("")

    for command in result.commands:
        lines.append(str(command))

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import a NuSMV/SMV file and print the derived guarded-command model.",
        epilog="""
Example:
  zksmv model.smv

Use --ast to print the parsed SMV AST summary instead of the derived guarded commands.
        """,
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="Input .smv file (default: stdin)",
    )
    parser.add_argument(
        "--ast",
        action="store_true",
        help="Print the parsed SMV AST summary instead of the lowered guarded commands.",
    )
    args = parser.parse_args(argv)

    try:
        text = args.file.read()
        if not text.strip():
            print("Error: empty input", file=sys.stderr)
            return 1
        model = parse_smv(text)
        if args.ast:
            print(format_smv_model(model))
        else:
            print(format_gc_result_from_smv(model))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
