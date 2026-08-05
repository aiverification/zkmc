"""CLI for zkltl: derive a Büchi automaton from an LTL `spec:` via Spot and print it.

Reads a `.gc` file that declares atomic propositions (`ap NAME := ...`) and an LTL property
(`spec: "..."`), translates the negated property to a Büchi automaton with Spot's ltl2tgba,
and prints the resulting `automaton_init` + `trans`/`trans!` declarations. Useful for inspecting
(or materialising) the automaton that `zkverify`/`zkfarkas`/`zkexplicit` derive automatically.
"""

import argparse
import sys

from .parser import parse_with_constants


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive a Büchi automaton from an LTL 'spec:' using Spot (ltl2tgba) and print "
                    "it as automaton_init + trans/trans! declarations.",
        epilog="""
Example:
  zkltl program.gc
  zkltl program.gc --const maxAttempts=3

Requires Spot's ltl2tgba on PATH (brew install spot / apt install spot), or set
ZKTERM_LTL2TGBA to its path.
        """,
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="Input .gc file with `ap`/`spec` declarations (default: stdin)",
    )
    parser.add_argument(
        "--const",
        action="append",
        metavar="NAME=VALUE",
        help="Override constant value (e.g., --const maxVal=5). Can be used multiple times.",
    )
    parser.add_argument(
        "--ltl2tgba",
        metavar="PATH",
        default=None,
        help="Path to the ltl2tgba binary (overrides PATH / ZKTERM_LTL2TGBA).",
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

        result = parse_with_constants(
            text,
            const_overrides=const_overrides if const_overrides else None,
            resolve_ltl=True,
            ltl2tgba_path=args.ltl2tgba,
        )

        if result.ltl_formula is None:
            print("Error: input has no LTL `spec:` declaration to derive an automaton from.", file=sys.stderr)
            return 1

        print(f"// property: {result.ltl_formula}")
        print(f"// automaton for the negation, {len(result.automaton_transitions)} transition(s)")
        if result.aps:
            for name, comps in result.aps.items():
                guard_str = " && ".join(str(c) for c in comps)
                print(f"//   ap {name} := {guard_str}")
        print()

        init = result.automaton_initial_states or []
        print(f"automaton_init: {', '.join(init)}")
        for t in result.automaton_transitions:
            fair = "!" if t.is_fair else ""
            guard_str = " && ".join(str(g) for g in t.guards) if t.guards else "true"
            print(f"trans{fair}({t.from_state}, {t.to_state}): {guard_str}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
