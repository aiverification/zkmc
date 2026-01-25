"""CLI for zkrank: encode ranking functions as matrix forms."""

import argparse
import sys
from typing import TextIO

from .parser import parse_with_constants
from .ranking_encoder import (
    encode_ranking_functions, RankingCaseEncoding, InfinityCaseEncoding,
    RankingFunctionEncoding
)
from .ranking_validator import validate_ranking_function


def format_inequality(coeffs: list[int], variables: list[str], const: int, strict: bool = False) -> str:
    """Format a single inequality with variable names.

    E.g., coeffs=[2, -1], vars=[x, y], const=2 -> "2x - y <= 2"
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


def format_expression(coeffs: list[int], variables: list[str], const: int) -> str:
    """Format a linear expression with variable names.

    E.g., coeffs=[-1, 2], vars=[x, y], const=10 -> "-x + 2y + 10"
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

    # Add constant if non-zero or if no variable terms
    if const != 0 or not terms:
        const_term = str(const)
        if const > 0 and terms:
            const_term = f"+{const}"
        terms.append(const_term)

    if not terms:
        return "0"

    # Build expression with proper +/- signs
    expr_parts = []
    for i, term in enumerate(terms):
        if i == 0:
            expr_parts.append(term)
        elif term.startswith("-") or term.startswith("+"):
            expr_parts.append(f" {term}")
        else:
            expr_parts.append(f" + {term}")

    return "".join(expr_parts).strip()


def format_ranking_case(
    case_enc: RankingCaseEncoding,
    case_num: int,
    variables: list[str],
    symbolic: bool
) -> str:
    """Format one finite ranking case encoding.

    Paper notation: V(x, q) = w_j x + u_j  if  C_j x ≤ d_j
    """
    lines = [f"\nFinite Case {case_num}:"]

    if symbolic:
        # Symbolic format
        if case_enc.C_j.shape[0] > 0:
            lines.append("  Guard:")
            for row, const in zip(case_enc.C_j, case_enc.d_j):
                ineq = format_inequality(list(row), variables, int(const), strict=False)
                lines.append(f"    {ineq}")
        else:
            lines.append("  Guard: true")

        expr = format_expression(list(case_enc.w_j), variables, case_enc.u_j)
        lines.append(f"  Expression: {expr}")
    else:
        # Matrix format (default) - paper notation
        if case_enc.C_j.shape[0] > 0:
            lines.append("  Guard C_j x <= d_j:")
            lines.append("    C_j =")
            for row in case_enc.C_j:
                lines.append(f"      [{' '.join(f'{v:3d}' for v in row)}]")
            lines.append(f"    d_j = [{' '.join(f'{v:3d}' for v in case_enc.d_j)}]")
        else:
            lines.append("  Guard: true (no constraints)")

        lines.append("  Expression w_j x + u_j:")
        lines.append(f"    w_j = [{' '.join(f'{v:3d}' for v in case_enc.w_j)}]")
        lines.append(f"    u_j = {case_enc.u_j}")

    return "\n".join(lines)


def format_infinity_case(
    case_enc: InfinityCaseEncoding,
    case_num: int,
    variables: list[str],
    symbolic: bool
) -> str:
    """Format one infinity ranking case encoding.

    Paper notation: V(x, q) = +∞  if  E_k x ≤ f_k
    """
    lines = [f"\nInfinity Case {case_num}:"]

    if symbolic:
        # Symbolic format
        if case_enc.E_k.shape[0] > 0:
            lines.append("  Guard:")
            for row, const in zip(case_enc.E_k, case_enc.f_k):
                ineq = format_inequality(list(row), variables, int(const), strict=False)
                lines.append(f"    {ineq}")
        else:
            lines.append("  Guard: true")

        lines.append("  Value: +∞")
    else:
        # Matrix format (default) - paper notation
        if case_enc.E_k.shape[0] > 0:
            lines.append("  Guard E_k x <= f_k:")
            lines.append("    E_k =")
            for row in case_enc.E_k:
                lines.append(f"      [{' '.join(f'{v:3d}' for v in row)}]")
            lines.append(f"    f_k = [{' '.join(f'{v:3d}' for v in case_enc.f_k)}]")
        else:
            lines.append("  Guard: true (no constraints)")

        lines.append("  Value: +∞")

    return "\n".join(lines)


def format_ranking_function(
    enc: RankingFunctionEncoding,
    symbolic: bool
) -> str:
    """Format a complete ranking function encoding."""
    lines = [f"=== Ranking Function for State {enc.state} ==="]
    lines.append(f"Variables: [{', '.join(enc.variables)}]")
    lines.append(f"Finite cases: {len(enc.finite_cases)}")
    lines.append(f"Infinity cases: {len(enc.infinity_cases)}")

    # Format finite cases
    for i, case_enc in enumerate(enc.finite_cases, start=1):
        lines.append(format_ranking_case(case_enc, i, enc.variables, symbolic))

    # Format infinity cases
    for k, case_enc in enumerate(enc.infinity_cases, start=1):
        lines.append(format_infinity_case(case_enc, k, enc.variables, symbolic))

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Main entry point for zkrank."""
    parser = argparse.ArgumentParser(
        description="Encode ranking functions as matrix/vector forms.",
        epilog="""
Example:
  echo 'rank(q0): [] x > 0 -> x' | zkrank
  zkrank program.gc
  zkrank -s program.gc  # symbolic output with variable names
        """
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="Input file with ranking functions (default: stdin)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show parsed ranking functions"
    )
    parser.add_argument(
        "-s", "--symbolic",
        action="store_true",
        help="Output with variable names (e.g., 'x - z <= 0' instead of matrices)"
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip validation checks (disjointness, coverage, non-negativity)"
    )

    args = parser.parse_args(argv)

    try:
        text = args.file.read()
        if not text.strip():
            print("Error: empty input", file=sys.stderr)
            return 1

        result = parse_with_constants(text)

        if not result.ranking_functions:
            print("Error: no ranking functions found in input", file=sys.stderr)
            return 1

        if args.verbose:
            print("Parsed ranking functions:")
            for state, rf in result.ranking_functions.items():
                print(f"  {rf}")
            print()

        # Encode ranking functions
        encodings = encode_ranking_functions(result.ranking_functions)

        # Validate ranking functions (unless skipped)
        if not args.skip_validation:
            validation_errors = []
            for state, enc in encodings.items():
                is_valid, errors = validate_ranking_function(
                    enc.finite_cases,
                    enc.infinity_cases,
                    enc.variables
                )
                if not is_valid:
                    validation_errors.append(f"State {state}:")
                    for error in errors:
                        validation_errors.append(f"  - {error}")

            if validation_errors:
                print("Validation failed:", file=sys.stderr)
                for error in validation_errors:
                    print(error, file=sys.stderr)
                return 1

        # Output encodings
        for i, (state, enc) in enumerate(encodings.items()):
            if i > 0:
                print("\n")
            print(format_ranking_function(enc, symbolic=args.symbolic))

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
