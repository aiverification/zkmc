"""CLI for zkterm-tool: encode guarded commands as matrix inequalities."""

import argparse
import sys
from typing import TextIO

from .parser import parse
from .encoder import encode_program, TransitionEncoding


def format_inequality(coeffs: list[int], variables: list[str], const: int, strict: bool) -> str:
    """Format a single inequality with variable names.
    
    E.g., coeffs=[2, -1], vars=[x, x'], const=2 -> "2x - x' <= 2"
    """
    terms = []
    for coeff, var in zip(coeffs, variables):
        if coeff == 0:
            continue
        if coeff == 1:
            term = var
        elif coeff == -1:
            term = f"-{var}"
        else:
            term = f"{coeff}{var}"
        terms.append(term)
    
    if not terms:
        lhs = "0"
    else:
        # Build LHS with proper +/- signs
        lhs_parts = []
        for i, term in enumerate(terms):
            if i == 0:
                lhs_parts.append(term)
            elif term.startswith("-"):
                lhs_parts.append(f" - {term[1:]}")
            else:
                lhs_parts.append(f" + {term}")
        lhs = "".join(lhs_parts)
    
    op = "<" if strict else "<="
    return f"{lhs} {op} {const}"


def format_encoding(enc: TransitionEncoding, index: int | None = None, symbolic: bool = False) -> str:
    """Format a transition encoding as readable text."""
    lines = []
    
    if index is not None:
        lines.append(f"=== Transition {index + 1} ===")
    
    full_vars = enc.full_variables()
    lines.append(f"Variables x = [{', '.join(full_vars)}]")
    
    if enc.A.shape[0] > 0:
        lines.append(f"\nNon-strict inequalities Ax ≤ b:")
        if symbolic:
            for row, const in zip(enc.A, enc.b):
                lines.append(f"  {format_inequality(list(row), full_vars, int(const), strict=False)}")
        else:
            lines.append(f"A =")
            for row in enc.A:
                lines.append(f"  [{' '.join(f'{v:3d}' for v in row)}]")
            lines.append(f"b = [{' '.join(f'{v:3d}' for v in enc.b)}]")
    else:
        lines.append("\nNo non-strict inequalities")
    
    if enc.C.shape[0] > 0:
        lines.append(f"\nStrict inequalities Cx < d:")
        if symbolic:
            for row, const in zip(enc.C, enc.d):
                lines.append(f"  {format_inequality(list(row), full_vars, int(const), strict=True)}")
        else:
            lines.append(f"C =")
            for row in enc.C:
                lines.append(f"  [{' '.join(f'{v:3d}' for v in row)}]")
            lines.append(f"d = [{' '.join(f'{v:3d}' for v in enc.d)}]")
    else:
        lines.append("\nNo strict inequalities")
    
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Encode guarded commands as matrix/vector inequality constraints.",
        epilog="""
Example:
  echo '[] y < z -> y = y + 1' | zkterm
  zkterm program.gc
  zkterm -s program.gc  # symbolic output with variable names
        """
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="Input file with guarded commands (default: stdin)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show parsed commands"
    )
    parser.add_argument(
        "-s", "--symbolic",
        action="store_true",
        help="Output inequalities with variable names (e.g., 2x - x' <= 2)"
    )
    parser.add_argument(
        "-n", "--non-strict",
        action="store_true",
        help="Convert strict inequalities to non-strict using integer semantics (x < c → x ≤ c-1)"
    )
    
    args = parser.parse_args(argv)
    
    try:
        text = args.file.read()
        if not text.strip():
            print("Error: empty input", file=sys.stderr)
            return 1
        
        commands = parse(text)
        
        if args.verbose:
            print("Parsed commands:")
            for i, cmd in enumerate(commands):
                print(f"  {i + 1}. {cmd}")
            print()
        
        encodings = encode_program(commands, nonstrict_only=args.non_strict)
        
        for i, enc in enumerate(encodings):
            if i > 0:
                print("\n")
            print(format_encoding(enc, index=i if len(encodings) > 1 else None, symbolic=args.symbolic))
        
        return 0
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
