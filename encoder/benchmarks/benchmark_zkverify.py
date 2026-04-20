"""Benchmarks for zkverify (symbolic verification via Z3) and zkfarkas."""

import pytest
from zkterm_tool import parse_with_constants
from zkterm_tool.verifier import Verifier
from zkterm_tool.farkas_cli import extract_farkas_obligations
from .benchmark_config import get_all_cases


# Generate parametrized test cases from config
# Include tags in ID so they can be filtered with -k
VERIFY_CASES = [
    pytest.param(
        case.name, case.program_file, case.const_overrides, case.description,
        id=f"{case.name}-{case.program_file.replace('.gc', '')}[{','.join(case.tags)}]"
    )
    for case in get_all_cases()
]


@pytest.mark.benchmark(group="zkverify-total")
@pytest.mark.parametrize("name,program_file,const_overrides,description", VERIFY_CASES)
def test_zkverify_total(benchmark, program_loader, name, program_file, const_overrides, description):
    """Benchmark total zkverify time: parsing + encoding + Z3 solving.

    Measures end-to-end time for symbolic verification using Farkas lemma + Z3.
    This is the primary metric for zkverify performance.
    """
    # Load program
    program_text = program_loader(program_file)

    def run_verification():
        # Parse with constant overrides
        result = parse_with_constants(program_text, const_overrides=const_overrides)

        # Create verifier and verify
        verifier = Verifier(result)
        verification = verifier.verify_all()

        return verification

    # Run benchmark
    verification = benchmark(run_verification)

    # Verify it actually passed (sanity check)
    assert verification.passed, f"Benchmark {name} verification failed!"


@pytest.mark.benchmark(group="zkverify-phases")
@pytest.mark.parametrize("name,program_file,const_overrides,description", VERIFY_CASES)
def test_zkverify_parse_encode(benchmark, program_loader, name, program_file, const_overrides, description):
    """Benchmark parsing + encoding phase only (no Z3).

    Measures time to parse .gc file and encode to matrices.
    Useful for understanding parsing overhead vs Z3 solving overhead.
    """
    # Load program
    program_text = program_loader(program_file)

    def run_parse_encode():
        # Parse with constant overrides
        result = parse_with_constants(program_text, const_overrides=const_overrides)

        # Create verifier (this does encoding)
        verifier = Verifier(result)

        return verifier

    # Run benchmark
    verifier = benchmark(run_parse_encode)

    # Sanity check
    assert len(verifier.rank_encs) > 0


@pytest.mark.benchmark(group="zkverify-phases")
@pytest.mark.parametrize("name,program_file,const_overrides,description", VERIFY_CASES)
def test_zkverify_z3_only(benchmark, program_loader, name, program_file, const_overrides, description):
    """Benchmark Z3 solving phase only.

    Measures time spent in Z3 SMT solver across all obligations.
    Setup (parsing/encoding) is excluded from timing.
    """
    # Load program
    program_text = program_loader(program_file)

    # Setup: Parse and create verifier (outside timing)
    result = parse_with_constants(program_text, const_overrides=const_overrides)
    verifier = Verifier(result)

    def run_z3_only():
        # This calls Z3 for all obligations
        verification = verifier.verify_all()
        return verification

    # Run benchmark (setup already done above, outside benchmark)
    verification = benchmark(run_z3_only)
    assert verification.passed


@pytest.mark.benchmark(group="zkfarkas")
@pytest.mark.parametrize("name,program_file,const_overrides,description", VERIFY_CASES)
def test_zkfarkas_extraction(benchmark, program_loader, tmp_path, name, program_file, const_overrides, description):
    """Benchmark zkfarkas JSON extraction.

    Measures time to extract Farkas dual formulations as JSON.
    This is relevant for exporting obligations to external solvers.
    """
    # Load program
    program_text = program_loader(program_file)

    # Write to temporary file (extract_farkas_obligations needs a file path)
    temp_file = tmp_path / "program.gc"
    temp_file.write_text(program_text)

    def run_farkas_extraction():
        obligations_data = extract_farkas_obligations(str(temp_file), const_overrides=const_overrides)
        return obligations_data

    # Run benchmark
    obligations_data = benchmark(run_farkas_extraction)

    # Sanity check
    assert len(obligations_data) > 0
