"""Tests for Farkas JSON export functionality."""

import pytest
from zkterm_tool import extract_farkas_obligations


def test_extract_farkas_obligations_basic():
    """Test basic extraction of Farkas obligations to JSON format."""
    from pathlib import Path
    import tempfile

    program = """
        init: x = 0

        [] x < 5 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 5 -> 6 - x

        trans(q0, q0): x < 5
    """

    # Write to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.gc', delete=False) as f:
        f.write(program)
        temp_path = f.name

    try:
        obligations = extract_farkas_obligations(temp_path)

        # Should have 3 obligations: initial, well_defined, non_increasing
        assert len(obligations) == 3

        # Check structure of each obligation
        for obl in obligations:
            assert "obligation_type" in obl
            assert "matrices" in obl
            assert "dimensions" in obl

            matrices = obl["matrices"]
            assert "A_s" in matrices
            assert "b_s" in matrices
            assert "A_p" in matrices
            assert "b_p" in matrices
            assert "C_p" in matrices
            assert "d_p" in matrices

            # Check all matrices are lists
            assert isinstance(matrices["A_s"], list)
            assert isinstance(matrices["b_s"], list)
            assert isinstance(matrices["A_p"], list)
            assert isinstance(matrices["b_p"], list)
            assert isinstance(matrices["C_p"], list)
            assert isinstance(matrices["d_p"], list)

            dims = obl["dimensions"]
            assert "n_vars" in dims
            assert "n_lambda_s" in dims
            assert "n_lambda_p" in dims
            assert "n_mu_p" in dims

        # Check obligation types
        types = [o["obligation_type"] for o in obligations]
        assert "initial" in types
        assert "well_defined" in types
        assert "non_increasing" in types

    finally:
        Path(temp_path).unlink()


def test_extract_farkas_obligations_with_fair_transition():
    """Test extraction with fair transition (should have strictly_decreasing)."""
    from pathlib import Path
    import tempfile

    program = """
        init: x = 10

        [] x > 1 -> x = x - 1

        rank(q0):
            [] x > 0 -> x

        trans!(q0, q0): x > 1
    """

    with tempfile.NamedTemporaryFile(mode='w', suffix='.gc', delete=False) as f:
        f.write(program)
        temp_path = f.name

    try:
        obligations = extract_farkas_obligations(temp_path)

        # Should have 4 obligations: initial, well_defined, non_increasing, strictly_decreasing
        assert len(obligations) == 4

        types = [o["obligation_type"] for o in obligations]
        assert "strictly_decreasing" in types

        # Find the strictly_decreasing obligation
        sd_obl = next(o for o in obligations if o["obligation_type"] == "strictly_decreasing")

        # Check it has automaton transition info
        assert "automaton_transition" in sd_obl
        assert sd_obl["automaton_transition"]["from"] == "q0"
        assert sd_obl["automaton_transition"]["to"] == "q0"

    finally:
        Path(temp_path).unlink()


def test_extract_farkas_dimensions():
    """Test that dimensions are correctly reported."""
    from pathlib import Path
    import tempfile

    program = """
        init: x = 0 && y = 0

        [] x < 5 && y < 5 -> x = x + 1; y = y + 1

        rank(q0):
            [] x >= 0 && y >= 0 -> 6 - x + 6 - y

        trans(q0, q0): x < 5
    """

    with tempfile.NamedTemporaryFile(mode='w', suffix='.gc', delete=False) as f:
        f.write(program)
        temp_path = f.name

    try:
        obligations = extract_farkas_obligations(temp_path)

        # Check initial obligation
        initial = next(o for o in obligations if o["obligation_type"] == "initial")

        # Should have 2 variables (x, y)
        assert initial["dimensions"]["n_vars"] == 2

        # For well_defined, variables are in [x, y, x', y'] space (4 total)
        well_def = next(o for o in obligations if o["obligation_type"] == "well_defined")
        assert well_def["dimensions"]["n_vars"] == 4

    finally:
        Path(temp_path).unlink()


def test_extract_farkas_matrix_structure():
    """Test that matrix structures are valid."""
    from pathlib import Path
    import tempfile

    program = """
        init: x = 0

        [] x < 3 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 3 -> 4 - x

        trans(q0, q0): x < 3
    """

    with tempfile.NamedTemporaryFile(mode='w', suffix='.gc', delete=False) as f:
        f.write(program)
        temp_path = f.name

    try:
        obligations = extract_farkas_obligations(temp_path)

        initial = next(o for o in obligations if o["obligation_type"] == "initial")
        matrices = initial["matrices"]
        dims = initial["dimensions"]

        # A_s should be n_lambda_s × n_vars
        assert len(matrices["A_s"]) == dims["n_lambda_s"]
        if dims["n_lambda_s"] > 0:
            assert len(matrices["A_s"][0]) == dims["n_vars"]

        # b_s should be length n_lambda_s
        assert len(matrices["b_s"]) == dims["n_lambda_s"]

        # A_p should be n_lambda_p × n_vars
        assert len(matrices["A_p"]) == dims["n_lambda_p"]
        if dims["n_lambda_p"] > 0:
            assert len(matrices["A_p"][0]) == dims["n_vars"]

        # C_p should be n_mu_p × n_vars
        assert len(matrices["C_p"]) == dims["n_mu_p"]
        if dims["n_mu_p"] > 0:
            assert len(matrices["C_p"][0]) == dims["n_vars"]

    finally:
        Path(temp_path).unlink()
