"""CLI for importing a NuSMV/SMV file and printing the derived guarded commands."""

from __future__ import annotations

import argparse
import sys

from .hq_parser import parse_hq
from .hyperltl_support import require_hyperltl_parse_result_references, require_supported_hyperltl
from .hyperltl_types import HyperFormula
from .parser import ParseResult
from .smv_parser import parse_smv
from .smv_to_gc import smv_to_gc, smv_to_symbol_parse_result
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


def format_gc_result_from_smv(model: SmvModel, result: ParseResult | None = None) -> str:
    result = result or smv_to_gc(model)
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


def format_hyperltl_property(formula: HyperFormula) -> str:
    lines: list[str] = ["", "// HyperLTL property"]
    quantifiers = " ".join(str(q) for q in formula.quantifiers)
    lines.append(f"quantifiers: {quantifiers}")
    lines.append(f"traces: {', '.join(formula.traces())}")
    lines.append(f"atoms: {len(formula.atoms)}")
    for atom in formula.atoms:
        refs = ", ".join(str(ref) for ref in atom.references) or "<none>"
        lines.append(f"  {atom} refs=[{refs}]")
    lines.append("support: supported parser fragment")
    lines.append("reduction: not implemented in this step")
    return "\n".join(lines)


def read_hyperltl_input(path, text: str | None, result: ParseResult) -> HyperFormula | None:
    if path is not None and text is not None:
        raise ValueError("Use either --hyper or --hyper-text, not both")
    if path is None and text is None:
        return None

    source = path.read() if path is not None else text
    if not source or not source.strip():
        raise ValueError("empty HyperLTL input")

    formula = parse_hq(source)
    require_supported_hyperltl(formula)
    require_hyperltl_parse_result_references(formula, result)
    return formula


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import a NuSMV/SMV file and print the derived guarded-command model.",
        epilog="""
Example:
  zksmv model.smv

Use --ast to print the parsed SMV AST summary instead of the derived guarded commands.
Use --hyper property.hq or --hyper-text 'forall A. forall B. ...' to parse and
check a HyperLTL property. Reduction is intentionally not wired in yet.
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
    parser.add_argument(
        "--hyper",
        type=argparse.FileType("r"),
        default=None,
        help="Input .hq HyperLTL property file to parse/check.",
    )
    parser.add_argument(
        "--hyper-text",
        default=None,
        help="Inline HyperLTL property text to parse/check.",
    )
    args = parser.parse_args(argv)

    try:
        text = args.file.read()
        if not text.strip():
            print("Error: empty input", file=sys.stderr)
            return 1
        model = parse_smv(text)
        result = smv_to_symbol_parse_result(model) if args.ast else smv_to_gc(model)
        hyper_formula = read_hyperltl_input(args.hyper, args.hyper_text, result)
        if args.ast:
            output = format_smv_model(model)
        else:
            output = format_gc_result_from_smv(model, result)
        if hyper_formula is not None:
            output += format_hyperltl_property(hyper_formula)
        print(output)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
