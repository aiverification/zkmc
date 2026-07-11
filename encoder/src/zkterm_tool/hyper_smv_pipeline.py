"""Integrated `.smv + .hq` pipeline for ZKMC explicit model checking."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from .explicit_cli import DEFAULT_FIELD_SIZE, explicit_json_from_parse_result
from .hq_parser import parse_hq
from .hyperltl_reduce import reduce_hyperltl_to_ltl
from .parser import ParseResult, parse_with_constants
from .smv_cli import format_gc_parse_result
from .smv_parser import parse_smv
from .smv_to_gc import SmvLoweringStrategy, smv_to_gc


def parse_control_variables(values: list[str] | None) -> tuple[str, ...] | None:
    if not values:
        return None
    result: list[str] = []
    for raw in values:
        for name in raw.split(","):
            stripped = name.strip()
            if stripped and stripped not in result:
                result.append(stripped)
    return tuple(result)


def build_hyper_smv_parse_result(
    smv_text: str,
    hq_text: str,
    *,
    lowering_strategy: SmvLoweringStrategy = "partition",
    control_variables: tuple[str, ...] | None = None,
    proof_harness_text: str | None = None,
) -> ParseResult:
    """Build the full GC ParseResult from an SMV model and HyperLTL property.

    Steps:
      1. Parse SMV packet/system model.
      2. Lower SMV to guarded commands, using naive or partition lowering.
      3. Parse supported HyperLTL.
      4. Self-compose the model and reduce universal HyperLTL to an LTL `spec:`.
      5. Optionally merge manual `.gc` proof harness data.

    Ranking functions are not required here. The JSON/proof path resolves the
    reduced LTL spec to a Büchi automaton and synthesizes missing rankings with
    the same synthesizer used by `zkverify --synthesize`.
    """
    smv_model = parse_smv(smv_text)
    gc_model = smv_to_gc(
        smv_model,
        lowering_strategy=lowering_strategy,
        control_variables=control_variables,
    )
    hyper_formula = parse_hq(hq_text)
    reduced = reduce_hyperltl_to_ltl(gc_model, hyper_formula)

    if proof_harness_text is not None and proof_harness_text.strip():
        harness = parse_with_constants(proof_harness_text)
        merge_proof_harness(reduced, harness)

    return reduced


def merge_proof_harness(target: ParseResult, harness: ParseResult) -> ParseResult:
    """Merge `.gc` proof-harness declarations into a reduced HyperSMV model."""
    if harness.commands:
        raise ValueError("Proof harness must not define guarded commands")
    if harness.init_condition is not None:
        raise ValueError("Proof harness must not define an init condition")
    if harness.ltl_formula is not None:
        raise ValueError("Proof harness must not define an LTL spec")
    if harness.aps:
        raise ValueError("Proof harness must not define atomic propositions")
    if harness.automaton_transitions:
        raise ValueError(
            "Proof harness automata are not accepted here; the reduced LTL spec is resolved by Spot"
        )

    for name, value in harness.constants.items():
        existing = target.constants.get(name)
        if existing is not None and existing != value:
            raise ValueError(f"Conflicting constant in proof harness: {name}")
        target.constants[name] = value

    for name, type_def in harness.types.items():
        existing = target.types.get(name)
        if existing is not None and existing != type_def:
            raise ValueError(f"Conflicting type in proof harness: {name}")
        target.types[name] = type_def

    for state, ranking in harness.ranking_functions.items():
        if state in target.ranking_functions:
            raise ValueError(f"Duplicate ranking function for automaton state: {state}")
        target.ranking_functions[state] = ranking

    return target


def hyper_smv_explicit_json(
    smv_text: str,
    hq_text: str,
    *,
    proof_harness_text: str | None = None,
    lowering_strategy: SmvLoweringStrategy = "partition",
    control_variables: tuple[str, ...] | None = None,
    bounds: list[str] | None = None,
    field_size: int = DEFAULT_FIELD_SIZE,
    verbose: bool = False,
    sort_embeddings: bool = False,
    ltl2tgba_path: str | None = None,
    synthesize: bool = True,
    synth_kwargs: dict | None = None,
) -> tuple[dict, list[str], ParseResult]:
    result = build_hyper_smv_parse_result(
        smv_text,
        hq_text,
        lowering_strategy=lowering_strategy,
        control_variables=control_variables,
        proof_harness_text=proof_harness_text,
    )
    output, warnings = explicit_json_from_parse_result(
        result,
        bounds=bounds,
        field_size=field_size,
        verbose=verbose,
        sort_embeddings=sort_embeddings,
        ltl2tgba_path=ltl2tgba_path,
        synthesize=synthesize,
        synth_kwargs=synth_kwargs,
    )
    return output, warnings, result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the integrated SMV + HyperLTL pipeline for ZKMC explicit model checking.",
    )
    parser.add_argument("smv", type=Path, help="Input .smv model")
    parser.add_argument("hyper", type=Path, help="Input .hq HyperLTL property")
    parser.add_argument(
        "--rank-harness",
        type=Path,
        default=None,
        help=(
            "Optional .gc file containing manual rank(q) declarations. "
            "By default, missing rankings are synthesized automatically."
        ),
    )
    parser.add_argument(
        "--smv-lowering",
        choices=("naive", "partition"),
        default="partition",
        help="SMV lowering strategy. Partition lowering defaults to control variable pc.",
    )
    parser.add_argument(
        "--control-var",
        action="append",
        default=None,
        help="Control variable for partition lowering. May be repeated or comma-separated.",
    )
    parser.add_argument(
        "--emit-gc",
        action="store_true",
        help="Print the reduced self-composed GC model instead of explicit ZKMC JSON.",
    )
    parser.add_argument("--bounds", nargs="*", metavar="VAR:MIN:MAX", help="Override type-derived bounds.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--verbose", action="store_true", help="Include full states/transitions in JSON.")
    parser.add_argument("--sort-embeddings", action="store_true", help="Sort embedding lists numerically.")
    parser.add_argument("--field-size", type=int, default=DEFAULT_FIELD_SIZE, help="Prime field size.")
    parser.add_argument("--ltl2tgba", metavar="PATH", default=None, help="Path to Spot ltl2tgba.")
    parser.add_argument(
        "--no-synthesize",
        action="store_true",
        help="Disable automatic ranking synthesis. Then rankings must come from --rank-harness.",
    )
    parser.add_argument(
        "--mode",
        action="append",
        metavar="VAR",
        help="Force a synthesizer partition variable. May be repeated.",
    )
    parser.add_argument(
        "--max-regions",
        type=int,
        default=None,
        help="Cap synthesizer regions per partition.",
    )
    parser.add_argument(
        "--coeff-bound",
        type=int,
        default=None,
        help="Bound on absolute ranking coefficients searched by the synthesizer.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Write JSON output to this file.")
    parser.add_argument(
        "--prove-verify",
        default=None,
        help=(
            "Optional prover command to run after writing JSON, for example "
            "'target/release/prove_verify'. Requires --output."
        ),
    )
    args = parser.parse_args(argv)

    try:
        smv_text = args.smv.read_text()
        hq_text = args.hyper.read_text()
        harness_text = args.rank_harness.read_text() if args.rank_harness is not None else None
        control_variables = parse_control_variables(args.control_var)

        result = build_hyper_smv_parse_result(
            smv_text,
            hq_text,
            lowering_strategy=args.smv_lowering,
            control_variables=control_variables,
            proof_harness_text=harness_text,
        )

        if args.emit_gc:
            print(format_gc_parse_result(result, "// SMV + HyperLTL -> self-composed guarded commands"))
            return 0

        synth_kwargs = {}
        if args.mode:
            synth_kwargs["mode_vars"] = args.mode
        if args.max_regions is not None:
            synth_kwargs["max_regions"] = args.max_regions
        if args.coeff_bound is not None:
            synth_kwargs["coeff_bound"] = args.coeff_bound

        output, warnings = explicit_json_from_parse_result(
            result,
            bounds=args.bounds,
            field_size=args.field_size,
            verbose=args.verbose,
            sort_embeddings=args.sort_embeddings,
            ltl2tgba_path=args.ltl2tgba,
            synthesize=not args.no_synthesize,
            synth_kwargs=synth_kwargs,
        )
        for warning in warnings:
            print(f"Warning: {warning}", file=sys.stderr)

        rendered = json.dumps(output, indent=2) if args.pretty else json.dumps(output)
        if args.output is not None:
            args.output.write_text(rendered + "\n")
        else:
            print(rendered)

        if args.prove_verify is not None:
            if args.output is None:
                raise ValueError("--prove-verify requires --output because the prover consumes a JSON path")
            cmd = shlex.split(args.prove_verify) + [str(args.output)]
            completed = subprocess.run(cmd, text=True)
            return completed.returncode

        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
