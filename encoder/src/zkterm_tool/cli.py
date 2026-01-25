"""CLI for zkterm-tool: encode guarded commands, init conditions, and automaton transitions."""

import argparse
import sys
from typing import TextIO

from .parser import parse_with_constants
from .encoder import encode_program, encode_init, TransitionEncoding, InitEncoding
from .automaton_encoder import encode_automaton_transitions, AutomatonTransitionEncoding


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


def format_init_encoding(enc: InitEncoding, symbolic: bool) -> str:
    """Format initial condition encoding."""
    lines = ["=== Initial Condition ==="]
    lines.append(f"Variables: [{', '.join(enc.variables)}]")

    if enc.A_0.shape[0] > 0:
        lines.append("\nA_0 x <= b_0:")
        if symbolic:
            for row, const in zip(enc.A_0, enc.b_0):
                ineq = format_inequality(list(row), enc.variables, int(const), strict=False)
                lines.append(f"  {ineq}")
        else:
            lines.append("  A_0 =")
            for row in enc.A_0:
                lines.append(f"    [{' '.join(f'{v:3d}' for v in row)}]")
            lines.append(f"  b_0 = [{' '.join(f'{v:3d}' for v in enc.b_0)}]")
    else:
        lines.append("\nNo constraints (always true)")

    return "\n".join(lines)


def format_automaton_transition(enc: AutomatonTransitionEncoding, symbolic: bool) -> str:
    """Format one automaton transition encoding."""
    fair_mark = " (FAIR)" if enc.is_fair else ""
    lines = [f"\nTransition: {enc.from_state} -> {enc.to_state}{fair_mark}"]

    # Guard info (symbolic)
    if symbolic and enc.P.shape[0] > 0:
        guard_parts = []
        for row, const in zip(enc.P, enc.r):
            ineq = format_inequality(list(row), enc.variables, int(const), strict=False)
            guard_parts.append(ineq)
        lines.append(f"  Guard: {' && '.join(guard_parts)}")

    # Encoding P^(σ) x ≤ r^(σ)
    lines.append(f"  P^({enc.from_state},{enc.to_state}) x <= r^({enc.from_state},{enc.to_state}):")
    if symbolic:
        if enc.P.shape[0] > 0:
            for row, const in zip(enc.P, enc.r):
                ineq = format_inequality(list(row), enc.variables, int(const), strict=False)
                lines.append(f"    {ineq}")
        else:
            lines.append("    (no constraints - always true)")
    else:
        if enc.P.shape[0] > 0:
            lines.append("    P =")
            for row in enc.P:
                lines.append(f"      [{' '.join(f'{v:3d}' for v in row)}]")
            lines.append(f"    r = [{' '.join(f'{v:3d}' for v in enc.r)}]")
        else:
            lines.append("    (no constraints - always true)")

    lines.append(f"  In F (fair): {'YES' if enc.is_fair else 'NO'}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Encode guarded commands as matrix/vector inequality constraints. Strict inequalities are automatically converted to non-strict using integer semantics (x < c → x ≤ c-1).",
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
    args = parser.parse_args(argv)
    
    try:
        text = args.file.read()
        if not text.strip():
            print("Error: empty input", file=sys.stderr)
            return 1

        result = parse_with_constants(text)

        if args.verbose:
            if result.init_condition:
                print("Parsed initial condition:")
                guard_str = " && ".join(str(g) for g in result.init_condition)
                print(f"  init: {guard_str}")
                print()

            if result.commands:
                print("Parsed commands:")
                for i, cmd in enumerate(result.commands):
                    print(f"  {i + 1}. {cmd}")
                print()

            if result.automaton_transitions:
                print("Parsed automaton transitions:")
                for i, trans in enumerate(result.automaton_transitions):
                    print(f"  {i + 1}. {trans}")
                print()

        sections_printed = False

        # 1. Display initial condition if present
        if result.init_condition:
            init_enc = encode_init(result.init_condition)
            print(format_init_encoding(init_enc, symbolic=args.symbolic))
            sections_printed = True

        # 2. Display program transitions (guarded commands)
        if result.commands:
            if sections_printed:
                print("\n")

            encodings = encode_program(result.commands, nonstrict_only=True)

            for i, enc in enumerate(encodings):
                if i > 0:
                    print("\n")
                print(format_encoding(enc, index=i if len(encodings) > 1 else None, symbolic=args.symbolic))

            sections_printed = True

        # 3. Display automaton transitions if present
        if result.automaton_transitions:
            if sections_printed:
                print("\n")

            print("=== Automaton Transitions ===")
            print(f"Variables: [{', '.join(sorted({v for t in result.automaton_transitions for v in t.get_variables()}))}]")

            aut_encodings = encode_automaton_transitions(result.automaton_transitions)

            for enc in aut_encodings:
                print(format_automaton_transition(enc, symbolic=args.symbolic))

        if not sections_printed:
            print("Warning: No content to encode (no init, commands, or automaton transitions)", file=sys.stderr)

        return 0
    
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
