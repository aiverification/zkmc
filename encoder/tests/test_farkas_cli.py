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

        # Should have 1 obligation: update only (no infinity cases, so no initial_non_infinity)
        # update: 1 prog_trans × 1 aut_trans × 1 source_case × 1 target_case = 1
        assert len(obligations) == 1

        # Check structure of each obligation
        for obl in obligations:
            assert "obligation_type" in obl
            assert "matrices" in obl
            assert "dimensions" in obl

            matrices = obl["matrices"]
            assert "A_s" in matrices
            assert "b_s" in matrices
            assert "G_p" in matrices  # Public constraint matrix
            assert "h_p" in matrices  # Public constraint vector

            # Check all matrices are lists
            assert isinstance(matrices["A_s"], list)
            assert isinstance(matrices["b_s"], list)
            assert isinstance(matrices["G_p"], list)
            assert isinstance(matrices["h_p"], list)

            dims = obl["dimensions"]
            assert "n_vars" in dims
            assert "n_lambda_s" in dims
            assert "n_mu_s" in dims

        # Check obligation types (only update since no infinity cases)
        types = [o["obligation_type"] for o in obligations]
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

        # Should have 1 obligation: update only (no infinity cases)
        assert len(obligations) == 1

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

        # Should only have update obligations (no infinity cases)
        assert len(obligations) >= 1

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

        # Get first obligation (should be update)
        update = obligations[0]
        matrices = update["matrices"]
        dims = update["dimensions"]

        # A_s should be n_lambda_s × n_vars
        assert len(matrices["A_s"]) == dims["n_lambda_s"]
        if dims["n_lambda_s"] > 0:
            assert len(matrices["A_s"][0]) == dims["n_vars"]

        # b_s should be n × 1 column vector
        assert len(matrices["b_s"]) == dims["n_lambda_s"]
        if dims["n_lambda_s"] > 0:
            assert isinstance(matrices["b_s"][0], list)
            assert len(matrices["b_s"][0]) == 1

        # G_p should be n_mu_s × n_vars (public constraint matrix)
        assert isinstance(matrices["G_p"], list)
        assert len(matrices["G_p"]) == dims["n_mu_s"]
        if dims["n_mu_s"] > 0:
            assert isinstance(matrices["G_p"][0], list)
            assert len(matrices["G_p"][0]) == dims["n_vars"]

        # h_p should be n' × 1 column vector
        assert isinstance(matrices["h_p"], list)
        assert len(matrices["h_p"]) == dims["n_mu_s"]
        if dims["n_mu_s"] > 0:
            assert isinstance(matrices["h_p"][0], list)
            assert len(matrices["h_p"][0]) == 1

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

        # New system: 4 update obligations (2 source_cases × 2 target_cases)
        # No initial_non_infinity (no infinity cases)
        assert len(obligations) == 4

        # All should be update obligations
        for obl in obligations:
            assert obl["obligation_type"] == "update"

        # Each update should have source_case_idx and target_case_idx
        for obl in obligations:
            assert "source_case_idx" in obl
            assert "target_case_idx" in obl
            assert obl["source_case_idx"] in [0, 1]  # Two source cases
            assert obl["target_case_idx"] in [0, 1]  # Two target cases

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

        # Check witness structure (only lambda_s and mu_s, no lambda_p)
        assert "witness" in passing
        witness = passing["witness"]
        assert "lambda_s" in witness
        assert "mu_s" in witness
        assert "lambda_p" not in witness  # No lambda_p in new system

        # Check computed values (convenience value for ZK proofs)
        assert "computed_values" in passing
        computed = passing["computed_values"]
        assert "neg_b_s_T_lambda_s" in computed

        # Verify it's a number
        assert isinstance(computed["neg_b_s_T_lambda_s"], int)

    finally:
        Path(temp_path).unlink()


def test_witness_column_vector_format():
    """Test that witness vectors are exported as column vectors."""
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

        # Find obligation with witness
        obl_with_witness = next((o for o in obligations if o.get("satisfiable")), None)
        if obl_with_witness:
            witness = obl_with_witness["witness"]
            dims = obl_with_witness["dimensions"]

            # lambda_s should be n × 1 column vector
            assert isinstance(witness["lambda_s"], list)
            assert len(witness["lambda_s"]) == dims["n_lambda_s"]
            if dims["n_lambda_s"] > 0:
                assert isinstance(witness["lambda_s"][0], list)
                assert len(witness["lambda_s"][0]) == 1

            # mu_s should be n' × 1 column vector
            assert isinstance(witness["mu_s"], list)
            assert len(witness["mu_s"]) == dims["n_mu_s"]
            if dims["n_mu_s"] > 0:
                assert isinstance(witness["mu_s"][0], list)
                assert len(witness["mu_s"][0]) == 1
    finally:
        Path(temp_path).unlink()


def test_computed_values_column_vectors():
    """Test that computed product vectors are column vectors."""
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

        # Find obligation with witness
        obl_with_witness = next((o for o in obligations if o.get("satisfiable")), None)
        if obl_with_witness:
            computed = obl_with_witness["computed_values"]
            dims = obl_with_witness["dimensions"]

            # Scalars should remain scalars
            assert isinstance(computed["neg_b_s_T_lambda_s"], int)
            assert isinstance(computed["neg_h_p_T_mu_s"], int)

            # A_s_T_lambda_s should be m × 1 column vector
            assert isinstance(computed["A_s_T_lambda_s"], list)
            assert len(computed["A_s_T_lambda_s"]) == dims["n_vars"]
            if dims["n_vars"] > 0:
                assert isinstance(computed["A_s_T_lambda_s"][0], list)
                assert len(computed["A_s_T_lambda_s"][0]) == 1

            # G_p_T_mu_s should be m × 1 column vector
            assert isinstance(computed["G_p_T_mu_s"], list)
            assert len(computed["G_p_T_mu_s"]) == dims["n_vars"]
            if dims["n_vars"] > 0:
                assert isinstance(computed["G_p_T_mu_s"][0], list)
                assert len(computed["G_p_T_mu_s"][0]) == 1
    finally:
        Path(temp_path).unlink()
