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
    sort_embeddings=False,
    state_space=None,
    constants=None
) -> dict[str, Any]:
    """Convert violation sets and valid sets to JSON format.

    Args:
        violations: ViolationSets object containing both violation and valid sets
        embeddings: FieldEmbeddings object (always required)
        verification_checks: Optional VerificationChecks object
        verbose: If True, include full state dictionaries; if False, only embeddings
        sort_embeddings: If True, sort embedding lists numerically
        state_space: Optional StateSpace object for bounds information
        constants: Optional dict of constants for reproducibility

    Returns:
        Dictionary with embeddings (always), verification results, and optionally
        full state dictionaries (if verbose=True)

    Example output (default, compact):
        {
            "embeddings": {
                "E_init": [11, 12],
                "E_step": [8, 8, 9],
                "E_fairstep": [],
                "E_S0": [0],
                "E_T": [1, 2, 3, ...],
                "field_size": 52435875...,
                "max_embedding_S": 15,
                "max_embedding_SxS": 255,
                "embeddings_valid": true
            },
            "verification": {...},
            "metadata": {
                "set_sizes": {
                    "S": 16,        // E_S is implicitly [0, 1, 2, ..., 15]
                    "SxS": 256,     // E_SxS is implicitly [0, 1, 2, ..., 255]
                    ...
                },
                ...
            }
        }

        Note: E_S and E_SxS are not included because they're just [0, |S|) and
        [0, |SxS|) respectively - use metadata.set_sizes.S and .SxS instead.

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
    E_S0 = sorted(embeddings.E_S0) if sort_embeddings else embeddings.E_S0
    E_T = sorted(embeddings.E_T) if sort_embeddings else embeddings.E_T

    # Embeddings always included (compact representation)
    # Note: E_S and E_SxS are implicit (just range(|S|) and range(|S×S|))
    result = {
        "embeddings": {
            "E_init": E_init,
            "E_step": E_step,
            "E_fairstep": E_fairstep,
            "E_S0": E_S0,
            "E_T": E_T,
            "field_size": embeddings.field_size,
            "max_embedding_S": embeddings.max_embedding_S,
            "max_embedding_SxS": embeddings.max_embedding_SxS,
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

    # Add bounds information if available (for reproducibility)
    if state_space is not None:
        result["metadata"]["bounds"] = {
            var: {"min": state_space.bounds[var].min_value, "max": state_space.bounds[var].max_value}
            for var in state_space.variables
        }

    # Add constants if available (for reproducibility)
    if constants is not None and len(constants) > 0:
        result["metadata"]["constants"] = constants


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
        nargs="*",
        metavar="VAR:MIN:MAX",
        help="State space bounds for each variable (e.g., x:0:10 y:0:5). Optional if types defined."
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

    parser.add_argument(
        "--const",
        action="append",
        metavar="NAME=VALUE",
        help="Override constant value (e.g., --const maxVal=5). Can be used multiple times."
    )

    args = parser.parse_args(argv)

    try:
        # Read and parse file
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
        trans_encs = encode_program(result.commands, nonstrict_only=True, types=result.types) if result.commands else []

        # Get all variables from all components
        all_vars = set()

        # From ranking functions
        for enc in rank_encs.values():
            all_vars.update(enc.variables)

        # From automaton transitions
        aut_encs = encode_automaton_transitions(result.automaton_transitions)
        for enc in aut_encs:
            all_vars.update(enc.variables)

        # From program transitions
        for enc in trans_encs:
            all_vars.update(enc.variables)

        # From init condition (extract variables without encoding yet)
        if result.init_condition:
            for guard in result.init_condition:
                # Extract variables from guard expressions
                def collect_vars(expr):
                    from .ast_types import Var, BinOp, Neg
                    if isinstance(expr, Var):
                        all_vars.add(expr.name)
                    elif isinstance(expr, BinOp):
                        collect_vars(expr.left)
                        collect_vars(expr.right)
                    elif isinstance(expr, Neg):
                        collect_vars(expr.expr)

                collect_vars(guard.left)
                collect_vars(guard.right)

        variables = sorted(all_vars)

        # Now encode init with full variable list
        if result.init_condition:
            init_enc = encode_init(result.init_condition, variables, types=result.types)
        else:
            init_enc = None

        # Build bounds: start with types, then override with --bounds
        bounds_dict = {}

        # First, use type annotations as defaults
        for var_name, type_def in result.types.items():
            bounds_dict[var_name] = f"{var_name}:{type_def.min_value}:{type_def.max_value}"

        # Then, override with explicit --bounds args if provided
        if args.bounds:
            for bound_spec in args.bounds:
                # Parse bound to get variable name
                parts = bound_spec.split(":")
                if len(parts) == 3:
                    var_name = parts[0]
                    bounds_dict[var_name] = bound_spec
                else:
                    print(f"Error: Invalid bound specification '{bound_spec}'. Use format VAR:MIN:MAX.", file=sys.stderr)
                    return 1

        # Convert bounds_dict to list for create_state_space
        bounds_list = list(bounds_dict.values())

        # Check that all variables have bounds (either from types or CLI)
        missing_bounds = set(variables) - {b.split(":")[0] for b in bounds_list}
        if missing_bounds:
            print(f"Error: No bounds specified for variables: {', '.join(sorted(missing_bounds))}", file=sys.stderr)
            print(f"Either add type annotations or use --bounds {' '.join(f'{v}:MIN:MAX' for v in sorted(missing_bounds))}", file=sys.stderr)
            return 1

        # Create state space from bounds
        try:
            state_space = create_state_space(variables, bounds_list)
        except ValueError as e:
            print(f"Error in bounds: {e}", file=sys.stderr)
            return 1

        # Determine automaton initial states (Q_0)
        # Require explicit automaton_init declaration
        if result.automaton_initial_states is None:
            print("Error: No automaton initial states specified. Add 'automaton_init: q0, ...' to your program.", file=sys.stderr)
            return 1

        automaton_initial_states = result.automaton_initial_states

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

        # Warn if verification failed
        if not verification_checks.all_disjoint:
            print("Warning: Verification failed - some sets are not disjoint:", file=sys.stderr)
            if not verification_checks.init_disjoint:
                print(f"  - S0 ∩ B_init ≠ ∅ ({verification_checks.init_intersection_size} states in common)", file=sys.stderr)
            if not verification_checks.step_disjoint:
                print(f"  - T ∩ B_step ≠ ∅ ({verification_checks.step_intersection_size} transitions in common)", file=sys.stderr)
            if not verification_checks.fairstep_disjoint:
                print(f"  - T ∩ B_fairstep ≠ ∅ ({verification_checks.fairstep_intersection_size} transitions in common)", file=sys.stderr)
            print(file=sys.stderr)  # Empty line for readability

        # Always compute embeddings (default behavior)
        embeddings = compute_embeddings(violations, args.field_size)

        # Convert to JSON with verbose and sort_embeddings flags
        output = violations_to_json(
            violations,
            embeddings,
            verification_checks,
            args.verbose,
            args.sort_embeddings,
            state_space,
            result.constants
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
