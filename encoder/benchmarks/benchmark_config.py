"""Centralized configuration for all benchmarks.

This file defines:
1. Which .gc files to benchmark
2. Constant overrides for parametric benchmarks
3. Bounds for zkexplicit benchmarks
4. Grouping and labeling for result organization
"""

from typing import Dict, List, NamedTuple


class BenchmarkCase(NamedTuple):
    """Configuration for a single benchmark case."""
    name: str                          # Display name
    program_file: str                  # Path relative to benchmarks/programs/
    const_overrides: Dict[str, int]    # Constant overrides
    bounds: List[str] | None           # For zkexplicit: ["x:0:10"] or None (use type annotations)
    tags: List[str]                    # For filtering: ["small", "paper", "exp_backoff"]
    description: str                   # Human-readable description
    run_explicit: bool = True          # Set to False to skip zkexplicit benchmarks


# Benchmark configurations organized by category
BENCHMARK_CONFIGS = {

    # ===== Baseline Benchmarks =====
    "baselines": [
        BenchmarkCase(
            name="counter_small",
            program_file="counter_simple.gc",
            const_overrides={"maxVal": 10},
            bounds=["x:0:15"],
            tags=["baseline", "small"],
            description="Simple counter 0→10, establishes baseline performance",
            run_explicit=True
        ),
        BenchmarkCase(
            name="counter_medium",
            program_file="counter_simple.gc",
            const_overrides={"maxVal": 100},
            bounds=["x:0:110"],
            tags=["baseline", "medium"],
            description="Medium counter 0→100, tests scaling",
            run_explicit=True
        ),
    ],

    # ===== Exponential Backoff Family =====
    # Tests scaling with increasing problem size
    "exp_backoff_state_opt": [
        BenchmarkCase(
            name="exp_backoff_initial2_attempts2",
            program_file="../../exp_backoff_state_opt_small.gc",
            const_overrides={"initialDelay": 2},
            bounds=None,
            tags=["exp_backoff", "small", "paper"],
            description="Exponential backoff: 2 attempts, delay=2",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial4_attempts2",
            program_file="../../exp_backoff_state_opt_small.gc",
            const_overrides={"initialDelay": 4},
            bounds=None,
            tags=["exp_backoff", "small", "paper"],
            description="Exponential backoff: 2 attempts, delay=4",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial8_attempts2",
            program_file="../../exp_backoff_state_opt_small.gc",
            const_overrides={"initialDelay": 8},
            bounds=None,
            tags=["exp_backoff", "small", "paper"],
            description="Exponential backoff: 2 attempts, delay=8",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial2_attempts3",
            program_file="../../exp_backoff_state_opt.gc",
            const_overrides={"initialDelay": 2},
            bounds=None,
            tags=["exp_backoff", "paper"],
            description="Exponential backoff: 3 attempts, delay=2",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial4_attempts3",
            program_file="../../exp_backoff_state_opt.gc",
            const_overrides={"initialDelay": 4},
            bounds=None,
            tags=["exp_backoff", "paper"],
            description="Exponential backoff: 3 attempts, delay=4",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial8_attempts3",
            program_file="../../exp_backoff_state_opt.gc",
            const_overrides={"initialDelay": 8},
            bounds=None,
            tags=["exp_backoff", "paper"],
            description="Exponential backoff: 3 attempts, delay=8",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial64_attempts3",
            program_file="../../exp_backoff_state_opt.gc",
            const_overrides={"initialDelay": 64},
            bounds=None,
            tags=["exp_backoff", "large", "paper"],
            description="Exponential backoff: 3 attempts, delay=64",
            run_explicit=False
        ),
        BenchmarkCase(
            name="exp_backoff_initial128_attempts3",
            program_file="../../exp_backoff_state_opt.gc",
            const_overrides={"initialDelay": 128},
            bounds=None,
            tags=["exp_backoff", "large", "paper"],
            description="Exponential backoff: 3 attempts, delay=8",
            run_explicit=False
        ),
    ],

    "exp_backoff_guard_opt": [
        BenchmarkCase(
            name="exp_backoff_initial2_attempts2",
            program_file="../../exp_backoff_guard_opt.gc",
            const_overrides={"initialDelay": 2, "maxAttempts": 2},
            bounds=None,
            tags=["exp_backoff", "small", "paper"],
            description="Exponential backoff: 2 attempts, delay=2",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial4_attempts2",
            program_file="../../exp_backoff_guard_opt.gc",
            const_overrides={"initialDelay": 4, "maxAttempts": 2},
            bounds=None,
            tags=["exp_backoff", "small", "paper"],
            description="Exponential backoff: 2 attempts, delay=4",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial8_attempts2",
            program_file="../../exp_backoff_guard_opt.gc",
            const_overrides={"initialDelay": 8, "maxAttempts": 2},
            bounds=None,
            tags=["exp_backoff", "small", "paper"],
            description="Exponential backoff: 2 attempts, delay=8",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial2_attempts3",
            program_file="../../exp_backoff_guard_opt.gc",
            const_overrides={"initialDelay": 2},
            bounds=None,
            tags=["exp_backoff", "paper"],
            description="Exponential backoff: 3 attempts, delay=2",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial4_attempts3",
            program_file="../../exp_backoff_guard_opt.gc",
            const_overrides={"initialDelay": 4},
            bounds=None,
            tags=["exp_backoff", "paper"],
            description="Exponential backoff: 3 attempts, delay=4",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial8_attempts3",
            program_file="../../exp_backoff_guard_opt.gc",
            const_overrides={"initialDelay": 8},
            bounds=None,
            tags=["exp_backoff", "paper"],
            description="Exponential backoff: 3 attempts, delay=8",
            run_explicit=True
        ),
        BenchmarkCase(
            name="exp_backoff_initial64_attempts3",
            program_file="../../exp_backoff_guard_opt.gc",
            const_overrides={"initialDelay": 64},
            bounds=None,
            tags=["exp_backoff", "large", "paper"],
            description="Exponential backoff: 3 attempts, delay=64",
            run_explicit=False
        ),
        BenchmarkCase(
            name="exp_backoff_initial128_attempts3",
            program_file="../../exp_backoff_guard_opt.gc",
            const_overrides={"initialDelay": 128},
            bounds=None,
            tags=["exp_backoff", "large", "paper"],
            description="Exponential backoff: 3 attempts, delay=8",
            run_explicit=False
        ),
        BenchmarkCase(
            name="exp_backoff_initial64_attempts4",
            program_file="../../exp_backoff_guard_opt.gc",
            const_overrides={"initialDelay": 64, "maxAttempts": 4},
            bounds=None,
            tags=["exp_backoff", "large", "paper"],
            description="Exponential backoff: 4 attempts, delay=64",
            run_explicit=False
        ),
        BenchmarkCase(
            name="exp_backoff_initial128_attempts4",
            program_file="../../exp_backoff_guard_opt.gc",
            const_overrides={"initialDelay": 128, "maxAttempts": 4},
            bounds=None,
            tags=["exp_backoff", "large", "paper"],
            description="Exponential backoff: 4 attempts, delay=8",
            run_explicit=False
        ),
    ],

    # ===== User-provided programs =====
    # Users can add their own programs here
    "custom": [
        # Add your own benchmark cases here
        # Example:
        # BenchmarkCase(
        #     name="my_protocol",
        #     program_file="my_protocol.gc",
        #     const_overrides={},
        #     bounds=["var1:0:100", "var2:0:50"],
        #     tags=["custom"],
        #     description="My custom protocol benchmark"
        # ),
    ],
}


def get_all_cases() -> List[BenchmarkCase]:
    """Flatten all benchmark cases into a single list."""
    all_cases = []
    for category_cases in BENCHMARK_CONFIGS.values():
        all_cases.extend(category_cases)
    return all_cases


def filter_cases_by_tag(tag: str) -> List[BenchmarkCase]:
    """Get all benchmark cases with a specific tag."""
    return [case for case in get_all_cases() if tag in case.tags]


def get_case_by_name(name: str) -> BenchmarkCase | None:
    """Get a specific benchmark case by name."""
    for case in get_all_cases():
        if case.name == name:
            return case
    return None
