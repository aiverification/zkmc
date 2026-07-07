"""CLI for zksynth: synthesize ranking functions and print them.

Reads a `.gc` file (program + automaton, or an LTL `spec:`) that need not contain any `rank(...)`
blocks, synthesizes a single linear ranking per automaton state (Tier 1), and prints the resulting
`rank(q): …` declarations. Use `zkverify --synthesize` to synthesize and verify in one step.
"""

import argparse
import sys

from .parser import parse_with_constants
from .synth import synthesize_rankings, SynthesisError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthesize a linear ranking function per automaton state (Tier 1) and print "
                    "the rank(q) declarations.",
        epilog="""
Example:
  zksynth program.gc
  zkverify --synthesize program.gc   # synthesize and verify together

Tier 1 synthesizes one linear function per state over the variables' type-bounded domain;
programs needing piecewise/lexicographic rankings are not yet supported.
        """,
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="Input .gc file (program + automaton or LTL spec; default: stdin)",
    )
    parser.add_argument(
        "--const",
        action="append",
        metavar="NAME=VALUE",
        help="Override constant value (e.g., --const maxVal=5). Can be used multiple times.",
    )
    parser.add_argument(
        "--coeff-bound",
        type=int,
        default=None,
        help="Bound on |ranking coefficients| searched (default: 65536).",
    )
    parser.add_argument(
        "--mode",
        action="append",
        metavar="VAR",
        help="Force a variable to be a partition ('mode') variable (repeatable). Overrides "
             "auto-detection; the variable must be type-declared.",
    )
    parser.add_argument(
        "--max-regions",
        type=int,
        default=None,
        help="Cap on the number of regions a partition may have during auto-search (default: 64).",
    )
    args = parser.parse_args(argv)

    try:
        text = args.file.read()
        if not text.strip():
            print("Error: empty input", file=sys.stderr)
            return 1

        const_overrides: dict[str, int] = {}
        if args.const:
            for const_arg in args.const:
                try:
                    name, value = const_arg.split("=", 1)
                    const_overrides[name.strip()] = int(value.strip())
                except ValueError:
                    print(f"Error: Invalid constant override '{const_arg}'. Use format NAME=VALUE.", file=sys.stderr)
                    return 1

        if getattr(args.file, "name", "").endswith(".koat"):
            from .koat import import_koat
            result = import_koat(text)  # KoAT ITS -> guarded commands + termination automaton
        else:
            result = parse_with_constants(
                text,
                const_overrides=const_overrides if const_overrides else None,
                resolve_ltl=True,
            )

        kwargs = {}
        if args.coeff_bound is not None:
            kwargs["coeff_bound"] = args.coeff_bound
        if args.mode:
            kwargs["mode_vars"] = args.mode
        if args.max_regions is not None:
            kwargs["max_regions"] = args.max_regions

        try:
            rankings = synthesize_rankings(result, **kwargs)
        except SynthesisError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        for state in rankings:
            print(rankings[state])
            print()
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
