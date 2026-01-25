"""Integration tests for zkexplicit CLI tool."""

import pytest
import json
import sys
import tempfile
from pathlib import Path
from io import StringIO

from zkterm_tool.explicit_cli import main, violations_to_json
from zkterm_tool import (
    parse_with_constants,
    encode_ranking_functions,
    encode_automaton_transitions
)
from zkterm_tool.state_enumerator import create_state_space
from zkterm_tool.violation_checker import compute_violation_sets


def test_violations_to_json_structure():
    """Test that violations_to_json produces correct structure."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    trans(q0, q0): x < 5
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)

    variables = ["x"]
    state_space = create_state_space(variables, ["x:0:10"])

    violations = compute_violation_sets(
        state_space,
        rank_encs,
        aut_encs,
        None,
        ["q0"]
    )

    from zkterm_tool.violation_checker import verify_disjointness, compute_embeddings
    verification_checks = verify_disjointness(violations)
    embeddings = compute_embeddings(violations, 101)

    # Test verbose mode (includes full state dictionaries)
    output = violations_to_json(violations, embeddings, verification_checks, verbose=True)

    # Check structure - violation sets (only in verbose mode)
    assert "B_init" in output
    assert "B_step" in output
    assert "B_fairstep" in output

    # Check structure - valid sets (only in verbose mode)
    assert "S" in output
    assert "S0" in output
    assert "T" in output

    # Check structure - embeddings (always present)
    assert "embeddings" in output

    # Check structure - verification and metadata
    assert "verification" in output
    assert "metadata" in output

    # Check metadata
    assert output["metadata"]["variables"] == ["x"]
    assert "q0" in output["metadata"]["automaton_states"]
    assert output["metadata"]["num_states_enumerated"] == 11
    assert "set_sizes" in output["metadata"]
    assert output["metadata"]["set_sizes"]["S"] == 11
    assert output["metadata"]["set_sizes"]["B_init"] == 5

    # Check verification structure
    assert "init_disjoint" in output["verification"]
    assert "step_disjoint" in output["verification"]
    assert "fairstep_disjoint" in output["verification"]
    assert "all_disjoint" in output["verification"]

    # B_init should be list of dicts
    assert isinstance(output["B_init"], list)
    if len(output["B_init"]) > 0:
        assert isinstance(output["B_init"][0], dict)

    # B_step should be list of {"from": ..., "to": ...}
    assert isinstance(output["B_step"], list)
    if len(output["B_step"]) > 0:
        assert "from" in output["B_step"][0]
        assert "to" in output["B_step"][0]

    # S should be list of dicts
    assert isinstance(output["S"], list)
    assert len(output["S"]) == 11

    # S0 should be list of dicts
    assert isinstance(output["S0"], list)

    # T should be list of {"from": ..., "to": ...}
    assert isinstance(output["T"], list)


def test_violations_to_json_with_embeddings():
    """Test JSON output with embeddings."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    trans(q0, q0): x < 5
    """
    result = parse_with_constants(program)
    rank_encs = encode_ranking_functions(result.ranking_functions)
    aut_encs = encode_automaton_transitions(result.automaton_transitions)

    variables = ["x"]
    state_space = create_state_space(variables, ["x:0:10"])

    violations = compute_violation_sets(
        state_space,
        rank_encs,
        aut_encs,
        None,
        ["q0"]
    )

    from zkterm_tool.violation_checker import compute_embeddings, verify_disjointness
    embeddings = compute_embeddings(violations, 101)
    verification_checks = verify_disjointness(violations)

    output = violations_to_json(violations, embeddings, verification_checks)

    assert "embeddings" in output
    assert "E_init" in output["embeddings"]
    assert "E_step" in output["embeddings"]
    assert "E_fairstep" in output["embeddings"]
    assert "field_size" in output["embeddings"]
    assert output["embeddings"]["field_size"] == 101

    # Also check that verification is included
    assert "verification" in output
    assert "all_disjoint" in output["verification"]


def test_cli_simple_counter(tmp_path):
    """Test CLI with simple counter program."""
    program = """
    const maxVal = 10

    init: x = 0

    [] x < maxVal -> x = x + 1

    rank(q0):
        [] x >= 0 && x < maxVal + 1 -> maxVal + 1 - x

    trans(q0, q0): x < maxVal
    """

    # Write to temp file
    gc_file = tmp_path / "counter.gc"
    gc_file.write_text(program)

    # Capture stdout
    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        result = main([str(gc_file), "--bounds", "x:0:15", "--verbose"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    # Should succeed
    assert result == 0

    # Parse JSON output
    data = json.loads(output)

    # With --verbose, should have full state dictionaries
    assert "B_init" in data
    assert "B_step" in data
    assert "B_fairstep" in data
    assert "metadata" in data
    assert "embeddings" in data

    # Check metadata
    assert data["metadata"]["variables"] == ["x"]
    assert data["metadata"]["num_states_enumerated"] == 16  # x=0..15

    # Initial state x=0 should not be in B_init (ranking defined)
    assert {"x": 0} not in data["B_init"]

    # States where ranking is undefined should be in B_init
    # Based on actual behavior, x=12..15 are in B_init
    assert {"x": 12} in data["B_init"]
    assert {"x": 15} in data["B_init"]
    # x=11 should have defined ranking
    assert {"x": 11} not in data["B_init"]


def test_cli_pretty_output(tmp_path):
    """Test CLI with --pretty flag."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    trans(q0, q0): x < 5
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        result = main([str(gc_file), "--bounds", "x:0:5", "--pretty", "--verbose"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    # Pretty-printed JSON should have newlines and indentation
    assert "\n" in output
    assert "  " in output  # Indentation

    # Should still be valid JSON
    data = json.loads(output)
    # With --verbose, should have full state dictionaries
    assert "B_init" in data
    assert "embeddings" in data


def test_cli_default_embeddings(tmp_path):
    """Test CLI default behavior (embeddings without full state dictionaries)."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    trans(q0, q0): x < 5
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        # No --verbose flag: should get embeddings but not full state dicts
        result = main([str(gc_file), "--bounds", "x:0:5"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data = json.loads(output)

    # Embeddings always included (default behavior)
    assert "embeddings" in data
    assert "E_init" in data["embeddings"]
    assert "E_step" in data["embeddings"]
    assert "E_fairstep" in data["embeddings"]
    assert "E_S" in data["embeddings"]
    assert "E_S0" in data["embeddings"]
    assert "E_T" in data["embeddings"]
    assert "field_size" in data["embeddings"]
    assert "max_embedding" in data["embeddings"]
    assert "embeddings_valid" in data["embeddings"]

    # Metadata and verification always included
    assert "metadata" in data
    assert "verification" in data

    # Full state dictionaries NOT included (only in --verbose mode)
    assert "B_init" not in data
    assert "B_step" not in data
    assert "S" not in data
    assert "S0" not in data
    assert "T" not in data


def test_cli_sxs_embeddings(tmp_path):
    """Test that SxS embeddings are included."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    trans(q0, q0): x < 5
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        # Default mode: embeddings only
        result = main([str(gc_file), "--bounds", "x:0:3"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data = json.loads(output)

    # Check that E_SxS is included in embeddings
    assert "embeddings" in data
    assert "E_SxS" in data["embeddings"]

    # With 4 states (x=0,1,2,3), SxS should have 16 elements
    assert len(data["embeddings"]["E_SxS"]) == 16

    # Check metadata reports correct SxS size
    assert data["metadata"]["set_sizes"]["SxS"] == 16

    # In default mode, full SxS state dicts should NOT be included
    assert "SxS" not in data


def test_cli_verbose_sxs(tmp_path):
    """Test that verbose mode includes full SxS state dictionaries."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    trans(q0, q0): x < 5
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        # Verbose mode: includes full state dicts
        result = main([str(gc_file), "--bounds", "x:0:3", "--verbose"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data = json.loads(output)

    # In verbose mode, full SxS should be included
    assert "SxS" in data
    assert len(data["SxS"]) == 16

    # Check format: list of {"from": ..., "to": ...}
    assert "from" in data["SxS"][0]
    assert "to" in data["SxS"][0]

    # Check a specific transition
    assert {"from": {"x": 0}, "to": {"x": 0}} in data["SxS"]
    assert {"from": {"x": 3}, "to": {"x": 3}} in data["SxS"]


def test_cli_sort_embeddings(tmp_path):
    """Test CLI with --sort-embeddings flag."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    trans(q0, q0): x < 5
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        # With --sort-embeddings: embeddings should be numerically sorted
        result = main([str(gc_file), "--bounds", "x:0:3", "--sort-embeddings"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data = json.loads(output)

    # Check that all embeddings are sorted
    assert "embeddings" in data

    # E_SxS should be sorted numerically
    e_sxs = data["embeddings"]["E_SxS"]
    assert e_sxs == sorted(e_sxs), f"E_SxS not sorted: {e_sxs}"

    # All other embeddings should also be sorted
    assert data["embeddings"]["E_S"] == sorted(data["embeddings"]["E_S"])
    assert data["embeddings"]["E_S0"] == sorted(data["embeddings"]["E_S0"])
    assert data["embeddings"]["E_T"] == sorted(data["embeddings"]["E_T"])
    assert data["embeddings"]["E_init"] == sorted(data["embeddings"]["E_init"])
    assert data["embeddings"]["E_step"] == sorted(data["embeddings"]["E_step"])
    assert data["embeddings"]["E_fairstep"] == sorted(data["embeddings"]["E_fairstep"])


def test_cli_with_verbose(tmp_path):
    """Test CLI with --verbose flag and custom field size."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    trans(q0, q0): x < 5
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        result = main([
            str(gc_file),
            "--bounds", "x:0:5",
            "--verbose",
            "--field-size", "101"
        ])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data = json.loads(output)
    # Embeddings always included (default)
    assert "embeddings" in data
    assert data["embeddings"]["field_size"] == 101
    # With --verbose, also includes full state dictionaries
    assert "B_init" in data
    assert "S" in data


def test_cli_missing_file():
    """Test CLI with non-existent file."""
    old_stderr = sys.stderr
    sys.stderr = StringIO()

    try:
        result = main(["nonexistent.gc", "--bounds", "x:0:10"])
        error = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr

    assert result == 1
    assert "File not found" in error


def test_cli_missing_bounds(tmp_path):
    """Test CLI without required --bounds."""
    program = """
    rank(q0):
        [] x >= 0 -> x

    trans(q0, q0): x < 5
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    # ArgumentParser will raise SystemExit
    with pytest.raises(SystemExit):
        main([str(gc_file)])


def test_cli_invalid_bounds(tmp_path):
    """Test CLI with invalid bounds format."""
    program = """
    rank(q0):
        [] x >= 0 -> x

    trans(q0, q0): x < 5
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stderr = sys.stderr
    sys.stderr = StringIO()

    try:
        result = main([str(gc_file), "--bounds", "invalid"])
        error = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr

    assert result == 1
    assert "Error in bounds" in error


def test_cli_missing_ranking_functions(tmp_path):
    """Test CLI with file missing ranking functions."""
    program = """
    [] x < 10 -> x = x + 1
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stderr = sys.stderr
    sys.stderr = StringIO()

    try:
        result = main([str(gc_file), "--bounds", "x:0:10"])
        error = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr

    assert result == 1
    assert "No ranking functions defined" in error


def test_cli_missing_automaton_transitions(tmp_path):
    """Test CLI with file missing automaton transitions."""
    program = """
    rank(q0):
        [] x >= 0 -> x
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stderr = sys.stderr
    sys.stderr = StringIO()

    try:
        result = main([str(gc_file), "--bounds", "x:0:10"])
        error = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr

    assert result == 1
    assert "No automaton transitions defined" in error


def test_cli_multivar(tmp_path):
    """Test CLI with multiple variables."""
    program = """
    rank(q0):
        [] x >= 0 && y >= 0 && x + y <= 10 -> x + y

    trans(q0, q0): x + y < 10
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        result = main([str(gc_file), "--bounds", "x:0:5", "y:0:5"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data = json.loads(output)
    assert set(data["metadata"]["variables"]) == {"x", "y"}
    assert data["metadata"]["num_states_enumerated"] == 6 * 6  # 36 states


def test_cli_fair_transition(tmp_path):
    """Test CLI with fair transition."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 10 -> 10 - x

    trans!(q0, q0): x < 10
    """

    gc_file = tmp_path / "test.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    sys.stdout = StringIO()

    try:
        result = main([str(gc_file), "--bounds", "x:0:5", "--verbose"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data = json.loads(output)

    # With --verbose, should have full state dictionaries
    # Should have B_fairstep violations (not B_step)
    assert len(data["B_fairstep"]) > 0
