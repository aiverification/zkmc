"""CLI for zkits: import a KoAT `.koat` integer transition system and print it as guarded commands.

Shows the translation into the existing IR — the `pc` location variable, the guarded commands, and
the attached all-fair termination automaton — so you can inspect what `zkverify --synthesize` /
`zksynth` will operate on.
"""

import argparse
import sys

from .koat import import_koat


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import a KoAT .koat integer transition system and print the derived guarded "
                    "commands + termination automaton (for inspection).",
        epilog="""
Example:
  zkits program.koat
  zkverify --synthesize program.koat   # import, synthesize a ranking, and verify termination
        """,
    )
    parser.add_argument(
        "file",
        nargs="?",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="Input .koat file (default: stdin)",
    )
    args = parser.parse_args(argv)

    try:
        text = args.file.read()
        if not text.strip():
            print("Error: empty input", file=sys.stderr)
            return 1
        result = import_koat(text)

        pc = next(iter(result.types))
        L = result.types[pc].max_value
        print(f"// {L + 1} locations -> {pc} : 0..{L}; data variables unbounded (symbolic path)")
        for var, td in result.types.items():
            print(f"type {var}: {td.min_value}..{td.max_value}")
        init_str = " && ".join(str(c) for c in result.init_condition) if result.init_condition else "true"
        print(f"init: {init_str}")
        print()
        for cmd in result.commands:
            print(cmd)
        print()
        print(f"automaton_init: {', '.join(result.automaton_initial_states or [])}")
        for t in result.automaton_transitions:
            fair = "!" if t.is_fair else ""
            guard = " && ".join(str(g) for g in t.guards) if t.guards else "true"
            print(f"trans{fair}({t.from_state}, {t.to_state}): {guard}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
