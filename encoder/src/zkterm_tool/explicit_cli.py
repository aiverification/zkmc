"""CLI for explicit-state verification.

This module provides the zkexplicit command-line tool for computing
violation sets (B_init, B_step, B_fairstep) by explicit state enumeration.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .parser import parse_with_constants
from .encoder import encode_init, encode_program
from .ranking_encoder import encode_ranking_functions
from .automaton_encoder import encode_automaton_transitions
from .state_enumerator import create_state_space
from .violation_checker import compute_violation_sets, compute_embeddings, verify_disjointness


def violations_to_json(
    violations,
    embeddings,
    verification_checks=None,
    verbose=False,
    sort_embeddings=False
) -> dict[str, Any]:
    """Convert violation sets and valid sets to JSON format.

    Args:
        violations: ViolationSets object containing both violation and valid sets
        embeddings: FieldEmbeddings object (always required)
        verification_checks: Optional VerificationChecks object
        verbose: If True, include full state dictionaries; if False, only embeddings
        sort_embeddings: If True, sort embedding lists numerically

    Returns:
        Dictionary with embeddings (always), verification results, and optionally
        full state dictionaries (if verbose=True)

    Example output (default, compact):
        {
            "embeddings": {
                "E_init": [11, 12],
                "E_step": [8, 8, 9],
                "E_fairstep": [],
                "E_S": [0, 1, 2, ..., 15],
                "E_S0": [0],
                "E_T": [1, 2, 3, ...],
                "E_SxS": [0, 1, 2, ..., 255],
                "field_size": 52435875...,
                "max_embedding": 15,
                "embeddings_valid": true
            },
            "verification": {...},
            "metadata": {
                "set_sizes": {...},
                "state_space_bounds": {"x": {"min": 0, "max": 15}},
                ...
            }
        }

    Example output (verbose, includes full states):
        {
            "embeddings": {...},
            "B_init": [{"x": 11}, {"x": 12}, ...],
            "B_step": [{"from": {"x": 0}, "to": {"x": 11}}, ...],
            "S": [{"x": 0}, {"x": 1}, ...],
            "S0": [{"x": 0}],
            "T": [{"from": {"x": 0}, "to": {"x": 1}}, ...],
            "SxS": [{"from": {"x": 0}, "to": {"x": 0}}, ...],
            "verification": {...},
            "metadata": {...}
        }
    """
    # Prepare embeddings (optionally sorted)
    E_init = sorted(embeddings.E_init) if sort_embeddings else embeddings.E_init
    E_step = sorted(embeddings.E_step) if sort_embeddings else embeddings.E_step
    E_fairstep = sorted(embeddings.E_fairstep) if sort_embeddings else embeddings.E_fairstep
    E_S = sorted(embeddings.E_S) if sort_embeddings else embeddings.E_S
    E_S0 = sorted(embeddings.E_S0) if sort_embeddings else embeddings.E_S0
    E_T = sorted(embeddings.E_T) if sort_embeddings else embeddings.E_T
    E_SxS = sorted(embeddings.E_SxS) if sort_embeddings else embeddings.E_SxS

    # Embeddings always included (compact representation)
    result = {
        "embeddings": {
            "E_init": E_init,
            "E_step": E_step,
            "E_fairstep": E_fairstep,
            "E_S": E_S,
            "E_S0": E_S0,
            "E_T": E_T,
            "E_SxS": E_SxS,
            "field_size": embeddings.field_size,
            "max_embedding": embeddings.max_embedding,
            "embeddings_valid": embeddings.embeddings_valid
        },
        "metadata": {
            "variables": violations.variables,
            "automaton_states": violations.automaton_states,
            "num_states_enumerated": violations.num_states_enumerated,
            "num_transitions_checked": violations.num_transitions_checked,
            "set_sizes": {
                "S": len(violations.S),
                "S0": len(violations.S0),
                "T": len(violations.T),
                "SxS": len(violations.SxS),
                "B_init": len(violations.B_init),
                "B_step": len(violations.B_step),
                "B_fairstep": len(violations.B_fairstep)
            }
        }
    }

    # Verbose mode: include full state dictionaries
    if verbose:
        result["B_init"] = violations.B_init
        result["B_step"] = [
            {"from": s, "to": s_prime}
            for s, s_prime in violations.B_step
        ]
        result["B_fairstep"] = [
            {"from": s, "to": s_prime}
            for s, s_prime in violations.B_fairstep
        ]
        result["S"] = violations.S
        result["S0"] = violations.S0
        result["T"] = [
            {"from": s, "to": s_prime}
            for s, s_prime in violations.T
        ]
        result["SxS"] = [
            {"from": s, "to": s_prime}
            for s, s_prime in violations.SxS
        ]

    # Verification checks
    if verification_checks is not None:
        result["verification"] = {
            "init_disjoint": verification_checks.init_disjoint,
            "step_disjoint": verification_checks.step_disjoint,
            "fairstep_disjoint": verification_checks.fairstep_disjoint,
            "all_disjoint": verification_checks.all_disjoint,
            "init_intersection_size": verification_checks.init_intersection_size,
            "step_intersection_size": verification_checks.step_intersection_size,
            "fairstep_intersection_size": verification_checks.fairstep_intersection_size
        }

    return result


def main(argv: list[str] | None = None) -> int:
    """Main entry point for zkexplicit command.

    Args:
        argv: Command-line arguments (defaults to sys.argv)

    Returns:
        Exit code (0 for success, 1 for error)

    Example usage:
        zkexplicit program.gc --bounds x:0:10 y:0:5
        zkexplicit program.gc --bounds x:0:10 --verbose
        zkexplicit --pretty program.gc --bounds status:0:2 delay:0:100
    """
    parser = argparse.ArgumentParser(
        description="Enumerate explicit-state violation sets for ZK verification",
        epilog="""
Examples:
  zkexplicit program.gc --bounds x:0:10 y:0:5
  zkexplicit program.gc --bounds x:0:10 --verbose  # Include full state dicts
  zkexplicit program.gc --bounds x:0:10 --sort-embeddings  # Sort embeddings numerically
  zkexplicit --pretty program.gc --bounds status:0:2 delay:0:100
  zkexplicit program.gc --bounds x:0:10 --field-size 101  # Custom field size

Note: Embeddings are always computed. Use --verbose to also see full state dictionaries.
Use --sort-embeddings to sort embedding lists numerically instead of maintaining set order.
        """
    )

    parser.add_argument(
        "file",
        type=str,
        help="Input .gc file with program, ranking functions, and automaton"
    )

    parser.add_argument(
        "--bounds",
        nargs="+",
        required=True,
        metavar="VAR:MIN:MAX",
        help="State space bounds for each variable (e.g., x:0:10 y:0:5)"
    )

    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output with indentation"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include full state dictionaries in addition to embeddings (can be large)"
    )

    parser.add_argument(
        "--field-size",
        type=int,
        default=52435875175126190479447740508185965837690552500527637822603658699938581184513,
        help="Prime field size for embeddings (default: BLS12-381 scalar field)"
    )

    parser.add_argument(
        "--sort-embeddings",
        action="store_true",
        help="Sort embedding lists numerically (default: maintain order from sorted sets)"
    )

    args = parser.parse_args(argv)

    try:
        # Read and parse file
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            return 1

        text = file_path.read_text()
        result = parse_with_constants(text)

        # Check required components
        if not result.ranking_functions:
            print("Error: No ranking functions defined", file=sys.stderr)
            return 1

        if not result.automaton_transitions:
            print("Error: No automaton transitions defined", file=sys.stderr)
            return 1

        # Encode ranking functions
        rank_encs = encode_ranking_functions(result.ranking_functions)

        # Encode program transitions (for computing T)
        trans_encs = encode_program(result.commands, nonstrict_only=True) if result.commands else []

        # Get all variables from all components
        all_vars = set()

        # From ranking functions
        for enc in rank_encs.values():
            all_vars.update(enc.variables)

        # From automaton transitions
        aut_encs = encode_automaton_transitions(result.automaton_transitions)
        for enc in aut_encs:
            all_vars.update(enc.variables)

        # From init condition
        if result.init_condition:
            init_enc = encode_init(result.init_condition)
            all_vars.update(init_enc.variables)
        else:
            init_enc = None

        # From program transitions
        for enc in trans_encs:
            all_vars.update(enc.variables)

        variables = sorted(all_vars)

        # Create state space from bounds
        try:
            state_space = create_state_space(variables, args.bounds)
        except ValueError as e:
            print(f"Error in bounds: {e}", file=sys.stderr)
            return 1

        # Determine automaton initial states (Q_0)
        # For now, use all states that have ranking functions
        # TODO: Should this come from automaton definition?
        automaton_initial_states = list(rank_encs.keys())

        # Compute violation sets and valid sets
        violations = compute_violation_sets(
            state_space,
            rank_encs,
            aut_encs,
            init_enc,
            automaton_initial_states,
            trans_encs
        )

        # Verify disjointness
        verification_checks = verify_disjointness(violations)

        # Always compute embeddings (default behavior)
        embeddings = compute_embeddings(violations, args.field_size)

        # Convert to JSON with verbose and sort_embeddings flags
        output = violations_to_json(
            violations,
            embeddings,
            verification_checks,
            args.verbose,
            args.sort_embeddings
        )

        # Output
        if args.pretty:
            print(json.dumps(output, indent=2))
        else:
            print(json.dumps(output))

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
