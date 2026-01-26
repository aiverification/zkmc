"""Pytest fixtures for benchmark configuration and program loading."""

from pathlib import Path
import pytest

# Benchmark root directory
BENCHMARK_ROOT = Path(__file__).parent
PROGRAMS_DIR = BENCHMARK_ROOT / "programs"


@pytest.fixture
def program_loader():
    """Fixture that returns a function to load .gc programs.

    Returns a function: load_program(filename) -> str
    """
    def load_program(filename: str) -> str:
        """Load a .gc program file.

        Args:
            filename: Relative path from benchmarks/programs/

        Returns:
            Program text content
        """
        program_path = PROGRAMS_DIR / filename
        if not program_path.exists():
            raise FileNotFoundError(f"Benchmark program not found: {program_path}")
        return program_path.read_text()

    return load_program
