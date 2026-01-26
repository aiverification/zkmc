"""Benchmarks for zkexplicit (explicit-state verification)."""

import pytest
from zkterm_tool import parse_with_constants
from zkterm_tool.ranking_encoder import encode_ranking_functions
from zkterm_tool.automaton_encoder import encode_automaton_transitions
from zkterm_tool.encoder import encode_program, encode_init
from zkterm_tool.state_enumerator import create_state_space
from zkterm_tool.violation_checker import compute_violation_sets, compute_embeddings
from .benchmark_config import get_all_cases


# Generate parametrized test cases from config (filter cases with run_explicit=True)
# Include tags in ID so they can be filtered with -k
EXPLICIT_CASES = [
    pytest.param(
        case.name, case.program_file, case.const_overrides, case.bounds, case.description,
        id=f"{case.name}-{case.program_file.replace('../../', '').replace('.gc', '')}[{','.join(case.tags)}]"
    )
    for case in get_all_cases()
    if case.run_explicit
]


@pytest.mark.benchmark(group="zkexplicit-total")
@pytest.mark.parametrize("name,program_file,const_overrides,bounds,description", EXPLICIT_CASES)
def test_zkexplicit_total(benchmark, program_loader, name, program_file, const_overrides, bounds, description):
    """Benchmark total zkexplicit time: parsing + enumeration + violation checking + embeddings.

    Measures end-to-end time for explicit-state verification.
    This is the primary metric for zkexplicit performance.
    """
    # Load program
    program_text = program_loader(program_file)

    def run_explicit_verification():
        # Parse with constant overrides
        result = parse_with_constants(program_text, const_overrides=const_overrides)

        # Encode all components
        rank_encs = encode_ranking_functions(result.ranking_functions)
        automaton_trans = encode_automaton_transitions(result.automaton_transitions)
        program_trans = encode_program(result.commands, nonstrict_only=True)

        # Collect variables from all encodings
        all_vars = set()
        for enc in rank_encs.values():
            all_vars.update(enc.variables)
        for enc in automaton_trans:
            all_vars.update(enc.variables)
        for enc in program_trans:
            all_vars.update(enc.variables)
        variables = sorted(all_vars)

        # Encode init with full variable list
        init_enc = encode_init(result.init_condition, variables) if result.init_condition else None

        # Build bounds: use type annotations if bounds=None, otherwise use provided bounds
        if bounds is None:
            bounds_list = [
                f"{var_name}:{type_def.min_value}:{type_def.max_value}"
                for var_name, type_def in result.types.items()
            ]
        else:
            bounds_list = bounds

        # Create state space from bounds
        state_space = create_state_space(variables, bounds_list)

        # Compute violation sets
        violation_sets = compute_violation_sets(
            state_space,
            rank_encs,
            automaton_trans,
            init_enc,
            result.automaton_initial_states or [],
            program_trans
        )

        return violation_sets

    # Run benchmark
    violation_sets = benchmark(run_explicit_verification)

    # Verify it actually computed something (sanity check)
    assert violation_sets.num_states_enumerated > 0


@pytest.mark.benchmark(group="zkexplicit-phases")
@pytest.mark.parametrize("name,program_file,const_overrides,bounds,description", EXPLICIT_CASES)
def test_zkexplicit_enumeration(benchmark, program_loader, name, program_file, const_overrides, bounds, description):
    """Benchmark state enumeration phase only.

    Measures time to enumerate all states within bounds.
    Setup (parsing, encoding, bounds parsing) is excluded from timing.
    """
    # Load program
    program_text = program_loader(program_file)

    # Setup: Parse and prepare (outside timing)
    result = parse_with_constants(program_text, const_overrides=const_overrides)

    # Collect variables from all components
    rank_encs = encode_ranking_functions(result.ranking_functions)
    automaton_trans = encode_automaton_transitions(result.automaton_transitions)
    program_trans = encode_program(result.commands, nonstrict_only=True)

    all_vars = set()
    for enc in rank_encs.values():
        all_vars.update(enc.variables)
    for enc in automaton_trans:
        all_vars.update(enc.variables)
    for enc in program_trans:
        all_vars.update(enc.variables)
    variables = sorted(all_vars)

    # Build bounds: use type annotations if bounds=None
    if bounds is None:
        bounds_list = [
            f"{var_name}:{type_def.min_value}:{type_def.max_value}"
            for var_name, type_def in result.types.items()
        ]
    else:
        bounds_list = bounds

    def run_enumeration():
        # Only this gets timed
        state_space = create_state_space(variables, bounds_list)
        return state_space

    # Run benchmark (setup already done above, outside benchmark)
    state_space = benchmark(run_enumeration)

    # Sanity check
    assert len(state_space.variables) > 0
    assert len(state_space.bounds) > 0


@pytest.mark.benchmark(group="zkexplicit-phases")
@pytest.mark.parametrize("name,program_file,const_overrides,bounds,description", EXPLICIT_CASES)
def test_zkexplicit_violation_checking(benchmark, program_loader, name, program_file, const_overrides, bounds, description):
    """Benchmark violation checking phase only (BOTTLENECK).

    Measures time to compute B_init, B_step, B_fairstep violation sets.
    This is the dominant bottleneck: O(r^(2n) × |δ|) state pair checking.
    Setup (parsing, encoding, state enumeration) is excluded from timing.
    """
    # Load program
    program_text = program_loader(program_file)

    # Setup: Parse, encode, and enumerate states (outside timing)
    result = parse_with_constants(program_text, const_overrides=const_overrides)

    # Encode all components
    rank_encs = encode_ranking_functions(result.ranking_functions)
    automaton_trans = encode_automaton_transitions(result.automaton_transitions)
    program_trans = encode_program(result.commands, nonstrict_only=True)

    # Collect variables from all encodings
    all_vars = set()
    for enc in rank_encs.values():
        all_vars.update(enc.variables)
    for enc in automaton_trans:
        all_vars.update(enc.variables)
    for enc in program_trans:
        all_vars.update(enc.variables)
    variables = sorted(all_vars)

    # Encode init with full variable list
    init_enc = encode_init(result.init_condition, variables) if result.init_condition else None

    # Build bounds: use type annotations if bounds=None
    if bounds is None:
        bounds_list = [
            f"{var_name}:{type_def.min_value}:{type_def.max_value}"
            for var_name, type_def in result.types.items()
        ]
    else:
        bounds_list = bounds

    # Create state space from bounds
    state_space = create_state_space(variables, bounds_list)

    # Store automaton initial states for use in benchmark
    automaton_initial_states = result.automaton_initial_states or []

    def run_violation_checking():
        # Only this gets timed (the bottleneck)
        violation_sets = compute_violation_sets(
            state_space,
            rank_encs,
            automaton_trans,
            init_enc,
            automaton_initial_states,
            program_trans
        )
        return violation_sets

    # Run benchmark (setup already done above, outside benchmark)
    violation_sets = benchmark(run_violation_checking)

    # Sanity check
    assert violation_sets.num_states_enumerated > 0


@pytest.mark.benchmark(group="zkexplicit-phases")
@pytest.mark.parametrize("name,program_file,const_overrides,bounds,description", EXPLICIT_CASES)
def test_zkexplicit_embeddings(benchmark, program_loader, name, program_file, const_overrides, bounds, description):
    """Benchmark field embedding computation phase only.

    Measures time to compute polynomial embeddings for ZK proofs.
    Setup (parsing, encoding, state enumeration, violation checking) is excluded from timing.
    """
    # Load program
    program_text = program_loader(program_file)

    # Setup: Parse, encode, enumerate, and compute violations (outside timing)
    result = parse_with_constants(program_text, const_overrides=const_overrides)

    # Encode all components
    rank_encs = encode_ranking_functions(result.ranking_functions)
    automaton_trans = encode_automaton_transitions(result.automaton_transitions)
    program_trans = encode_program(result.commands, nonstrict_only=True)

    # Collect variables from all encodings
    all_vars = set()
    for enc in rank_encs.values():
        all_vars.update(enc.variables)
    for enc in automaton_trans:
        all_vars.update(enc.variables)
    for enc in program_trans:
        all_vars.update(enc.variables)
    variables = sorted(all_vars)

    # Encode init with full variable list
    init_enc = encode_init(result.init_condition, variables) if result.init_condition else None

    # Build bounds: use type annotations if bounds=None
    if bounds is None:
        bounds_list = [
            f"{var_name}:{type_def.min_value}:{type_def.max_value}"
            for var_name, type_def in result.types.items()
        ]
    else:
        bounds_list = bounds

    # Create state space from bounds
    state_space = create_state_space(variables, bounds_list)

    # Store automaton initial states for use in violation checking
    automaton_initial_states = result.automaton_initial_states or []

    violation_sets = compute_violation_sets(
        state_space,
        rank_encs,
        automaton_trans,
        init_enc,
        automaton_initial_states,
        program_trans
    )

    def run_embeddings():
        # Only this gets timed
        embeddings = compute_embeddings(violation_sets)
        return embeddings

    # Run benchmark (setup already done above, outside benchmark)
    embeddings = benchmark(run_embeddings)

    # Sanity check
    assert embeddings.field_size > 0
