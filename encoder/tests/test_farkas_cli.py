"""Tests for Farkas JSON export functionality."""

import pytest
from pathlib import Path
import tempfile
from zkterm_tool import extract_farkas_obligations


def test_extract_farkas_obligations_basic():
    """Test basic extraction of Farkas obligations to JSON format."""
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

        # Should have 2 obligations: initial + update (1 prog_trans × 1 aut_trans × 1 source_case)
        assert len(obligations) == 2

        # Check structure of each obligation
        for obl in obligations:
            assert "obligation_type" in obl
            assert "matrices" in obl
            assert "dimensions" in obl

            matrices = obl["matrices"]
            assert "A_s" in matrices
            assert "b_s" in matrices
            assert "C" in matrices
            assert "d" in matrices
            assert "E_list" in matrices
            assert "f_list" in matrices

            # Check all matrices are lists
            assert isinstance(matrices["A_s"], list)
            assert isinstance(matrices["b_s"], list)
            assert isinstance(matrices["C"], list)
            assert isinstance(matrices["d"], list)
            assert isinstance(matrices["E_list"], list)
            assert isinstance(matrices["f_list"], list)

            # E_list and f_list should have same length (number of disjuncts)
            assert len(matrices["E_list"]) == len(matrices["f_list"])

            dims = obl["dimensions"]
            assert "n_vars" in dims
            assert "n_lambda_s" in dims
            assert "n_middle" in dims
            assert "n_disjuncts" in dims
            assert "n_mu_p" in dims

        # Check obligation types
        types = [o["obligation_type"] for o in obligations]
        assert "initial" in types
        assert "update" in types

    finally:
        Path(temp_path).unlink()


def test_extract_farkas_obligations_with_fair_transition():
    """Test extraction with fair transition (should set is_fair flag)."""
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

        # Should have 2 obligations: initial + update
        assert len(obligations) == 2

        # Find the update obligation
        update_obl = next(o for o in obligations if o["obligation_type"] == "update")

        # Check it has automaton transition info
        assert "automaton_transition" in update_obl
        assert update_obl["automaton_transition"]["from"] == "q0"
        assert update_obl["automaton_transition"]["to"] == "q0"

        # Check is_fair flag
        assert "is_fair" in update_obl
        assert update_obl["is_fair"] == True

    finally:
        Path(temp_path).unlink()


def test_extract_farkas_dimensions():
    """Test that dimensions are correctly reported."""
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

        # Should have 2 variables (x, y) - initial is in [x] space
        assert initial["dimensions"]["n_vars"] == 2

        # For update, variables are in [x, y, x', y'] space (4 total)
        update = next(o for o in obligations if o["obligation_type"] == "update")
        assert update["dimensions"]["n_vars"] == 4

    finally:
        Path(temp_path).unlink()


def test_extract_farkas_matrix_structure():
    """Test that matrix structures are valid."""
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

        # C should be n_middle × n_vars
        assert len(matrices["C"]) == dims["n_middle"]
        if dims["n_middle"] > 0:
            assert len(matrices["C"][0]) == dims["n_vars"]

        # E_list should have n_disjuncts elements
        assert len(matrices["E_list"]) == dims["n_disjuncts"]

        # Each E_k should be a matrix (list of lists)
        for E_k in matrices["E_list"]:
            assert isinstance(E_k, list)
            if len(E_k) > 0:
                assert isinstance(E_k[0], list)
                assert len(E_k[0]) == dims["n_vars"]

        # f_list should have n_disjuncts elements
        assert len(matrices["f_list"]) == dims["n_disjuncts"]

        # Each f_k should be a vector (list)
        for f_k in matrices["f_list"]:
            assert isinstance(f_k, list)

    finally:
        Path(temp_path).unlink()


def test_multi_case_ranking_function():
    """Test with multi-case ranking functions."""
    program = """
        init: x = 0

        [] x < 10 -> x = x + 1

        rank(q0):
            [] x >= 0 && x < 5 -> 10 - x
            [] x >= 5 && x < 11 -> 15 - x

        trans(q0, q0): x < 10
    """

    with tempfile.NamedTemporaryFile(mode='w', suffix='.gc', delete=False) as f:
        f.write(program)
        temp_path = f.name

    try:
        obligations = extract_farkas_obligations(temp_path)

        # Should have: 1 initial + 2 updates (one per source case)
        assert len(obligations) == 3

        initial = next(o for o in obligations if o["obligation_type"] == "initial")

        # Initial should have 2 disjuncts (one per ranking case)
        assert initial["dimensions"]["n_disjuncts"] == 2

        # Update obligations should each have 2 disjuncts (one per target case)
        updates = [o for o in obligations if o["obligation_type"] == "update"]
        assert len(updates) == 2
        for update in updates:
            assert update["dimensions"]["n_disjuncts"] == 2

    finally:
        Path(temp_path).unlink()


def test_witness_structure():
    """Test that witness structure is correct."""
    program = """
        init: x = 0

        [] x < 5 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 5 -> 6 - x

        trans(q0, q0): x < 5
    """

    with tempfile.NamedTemporaryFile(mode='w', suffix='.gc', delete=False) as f:
        f.write(program)
        temp_path = f.name

    try:
        obligations = extract_farkas_obligations(temp_path)

        # Find a passing obligation with witness
        passing = next((o for o in obligations if o["satisfiable"]), None)
        assert passing is not None

        # Check witness structure (only lambda_s and mu_p, no lambda_p)
        assert "witness" in passing
        witness = passing["witness"]
        assert "lambda_s" in witness
        assert "mu_p" in witness
        assert "lambda_p" not in witness  # No lambda_p in new system

        # Check computed values
        assert "computed_values" in passing
        computed = passing["computed_values"]
        assert "alpha_p" in computed
        assert "beta_p" in computed
        assert "verification_check" in computed

        # Verification check should confirm correctness
        check = computed["verification_check"]
        assert "alpha_p_equals_zero" in check
        assert "beta_p_leq_minus_one" in check
        assert check["alpha_p_equals_zero"] == True
        assert check["beta_p_leq_minus_one"] == True

    finally:
        Path(temp_path).unlink()
