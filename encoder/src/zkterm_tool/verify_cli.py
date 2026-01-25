"""CLI for zkverify: verify termination using Farkas lemma and Z3."""

import argparse
import sys
from pathlib import Path

from .parser import parse_with_constants
from .verifier import Verifier
from .ranking_encoder import encode_ranking_functions
from .ranking_validator import validate_ranking_function


def main(argv: list[str] | None = None) -> int:
    """Main entry point for zkverify command.

    Returns:
        0 if verification passed, 1 if failed or error
    """
    parser = argparse.ArgumentParser(
        description="Verify termination obligations using Farkas lemma and Z3 SMT solver",
        epilog="""
Example:
  zkverify program.gc
  zkverify --verbose program.gc  # Show Farkas witnesses
        """
    )
    parser.add_argument(
        "file",
        type=str,
        help="Input .gc file with program, ranking functions, and automaton transitions"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show Farkas witnesses for each obligation"
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip ranking function validation checks (disjointness, coverage, non-negativity)"
    )
    parser.add_argument(
        "--const",
        action="append",
        metavar="NAME=VALUE",
        help="Override constant value (e.g., --const maxVal=5). Can be used multiple times."
    )

    args = parser.parse_args(argv)

    try:
        # Read and parse input file
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            return 1

        # Parse constant overrides
        const_overrides = {}
        if args.const:
            for const_arg in args.const:
                try:
                    name, value = const_arg.split("=", 1)
                    const_overrides[name.strip()] = int(value.strip())
                except ValueError as e:
                    print(f"Error: Invalid constant override '{const_arg}'. Use format NAME=VALUE.", file=sys.stderr)
                    return 1

        text = file_path.read_text()
        result = parse_with_constants(text, const_overrides=const_overrides if const_overrides else None)

        # Validate ranking functions (unless skipped)
        if not args.skip_validation and result.ranking_functions:
            encodings = encode_ranking_functions(result.ranking_functions)
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
                print("Ranking function validation failed:", file=sys.stderr)
                for error in validation_errors:
                    print(error, file=sys.stderr)
                print("\nUse --skip-validation to bypass validation checks.", file=sys.stderr)
                return 1

        # Verify
        verifier = Verifier(result)
        verification = verifier.verify_all()

        # Output results
        if args.verbose:
            print(f"Verification Results for {args.file}")
            print("=" * 60)
            print()

            for i, obl in enumerate(verification.obligations, 1):
                status = "✓ PASS" if obl.passed else "✗ FAIL"
                print(f"[{i}/{len(verification.obligations)}] {status}: {obl.obligation_type}")

                if obl.program_transition_idx is not None:
                    print(f"     Program transition: {obl.program_transition_idx}")

                if obl.automaton_transition:
                    from_state, to_state = obl.automaton_transition
                    fair_str = " (FAIR)" if obl.is_fair else ""
                    print(f"     Automaton transition: {from_state} → {to_state}{fair_str}")

                if obl.source_ranking_state:
                    case_str = f" [case {obl.source_case_idx}]" if obl.source_case_idx is not None else ""
                    print(f"     Source state: {obl.source_ranking_state}{case_str}")

                if obl.target_ranking_state:
                    print(f"     Target state: {obl.target_ranking_state}")

                if args.verbose and obl.passed and obl.witness:
                    print(f"     Witness: {obl.witness}")

                print()

            print("=" * 60)

        # Print summary
        print(verification.summary())

        # Print failed obligations if any
        if not verification.passed:
            failed = verification.failed_obligations()
            print(f"\nFailed {len(failed)} obligation(s):")
            for obl in failed:
                print(f"  - {obl}")

        return 0 if verification.passed else 1

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
