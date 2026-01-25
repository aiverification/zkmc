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

    automaton_init: q0

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

    automaton_init: q0

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

    automaton_init: q0

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
    # Ranking function guard is x < 11, so x=11..15 are undefined
    assert {"x": 11} in data["B_init"]
    assert {"x": 12} in data["B_init"]
    assert {"x": 15} in data["B_init"]
    # x=10 should have defined ranking (last state covered by guard)
    assert {"x": 10} not in data["B_init"]


def test_cli_pretty_output(tmp_path):
    """Test CLI with --pretty flag."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    automaton_init: q0

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

    automaton_init: q0

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
    assert "E_S0" in data["embeddings"]
    assert "E_T" in data["embeddings"]
    assert "field_size" in data["embeddings"]
    assert "max_embedding_S" in data["embeddings"]
    assert "max_embedding_SxS" in data["embeddings"]
    assert "embeddings_valid" in data["embeddings"]

    # E_S and E_SxS are implicit (not included in output)
    assert "E_S" not in data["embeddings"]
    assert "E_SxS" not in data["embeddings"]

    # Metadata and verification always included
    assert "metadata" in data
    assert "verification" in data

    # Can compute E_S and E_SxS from sizes
    assert data["metadata"]["set_sizes"]["S"] == 6
    assert data["metadata"]["set_sizes"]["SxS"] == 36
    # E_S = [0, 1, 2, 3, 4, 5], E_SxS = [0, 1, ..., 35]

    # Verify max embeddings match set sizes
    assert data["embeddings"]["max_embedding_S"] == 5  # |S| - 1 = 6 - 1
    assert data["embeddings"]["max_embedding_SxS"] == 35  # |S×S| - 1 = 36 - 1

    # Full state dictionaries NOT included (only in --verbose mode)
    assert "B_init" not in data
    assert "B_step" not in data
    assert "S" not in data
    assert "S0" not in data
    assert "T" not in data


def test_cli_sxs_embeddings(tmp_path):
    """Test that SxS size is reported (E_SxS is implicit)."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    automaton_init: q0

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

    # E_SxS is NOT included (implicit: it's just [0, 1, 2, ..., |SxS|-1])
    assert "embeddings" in data
    assert "E_SxS" not in data["embeddings"]

    # Check metadata reports correct SxS size
    # With 4 states (x=0,1,2,3), SxS should have 16 elements
    assert data["metadata"]["set_sizes"]["SxS"] == 16
    # E_SxS is implicitly [0, 1, 2, ..., 15]

    # In default mode, full SxS state dicts should NOT be included
    assert "SxS" not in data


def test_cli_verbose_sxs(tmp_path):
    """Test that verbose mode includes full SxS state dictionaries."""
    program = """
    rank(q0):
        [] x >= 0 && x <= 5 -> 5 - x

    automaton_init: q0

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

    automaton_init: q0

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

    # E_S and E_SxS are not included (implicit: they're just range(|S|) and range(|SxS|))
    assert "E_S" not in data["embeddings"]
    assert "E_SxS" not in data["embeddings"]

    # All other embeddings should be sorted
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

    automaton_init: q0

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

    automaton_init: q0

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

    automaton_init: q0

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

    automaton_init: q0

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

    automaton_init: q0

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

def test_automaton_init_explicit(tmp_path):
    """Test automaton_init: explicitly specify initial states."""
    # Program with two automaton states, but only q0 in Q_0
    program = """
        init: x = 0

        [] x < 5 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 5 -> 5 - x

        rank(q1):
            [] x >= 0 && x <= 5 -> 10 - x

        automaton_init: q0

        trans(q0, q1): x >= 2
        trans(q1, q0): x < 3
    """

    gc_file = tmp_path / "test_automaton_init.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    try:
        sys.stdout = StringIO()
        result = main([str(gc_file), "--bounds", "x:0:5"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data = json.loads(output)

    # Check that B_init only considers q0 (not q1)
    # Since rank(q0) is well-defined for all x in [0,5], B_init should be empty
    # If we incorrectly used q1 as well, B_init would still be empty (both are well-defined)
    # So this test verifies the parsing, not the behavior difference
    assert "embeddings" in data
    assert "metadata" in data


def test_automaton_init_missing_error(tmp_path):
    """Test that missing automaton_init produces an error."""
    # Program without automaton_init should fail
    program = """
        init: x = 0

        [] x < 5 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 5 -> 5 - x

        rank(q1):
            [] x >= 0 && x <= 5 -> 10 - x

        trans(q0, q1): x >= 2
        trans(q1, q0): x < 3
    """

    gc_file = tmp_path / "test_automaton_default.gc"
    gc_file.write_text(program)

    old_stderr = sys.stderr
    try:
        sys.stderr = StringIO()
        result = main([str(gc_file), "--bounds", "x:0:5"])
        error = sys.stderr.getvalue()
    finally:
        sys.stderr = old_stderr

    # Should fail with error code 1
    assert result == 1
    assert "No automaton initial states specified" in error
    assert "automaton_init" in error


def test_automaton_init_subset_behavior(tmp_path):
    """Test that automaton_init changes which states are checked for B_init."""
    # Ranking function for q1 is undefined for x > 3
    # If q1 is in Q_0, then x=4,5 should be in B_init
    # If only q0 is in Q_0, then B_init should be empty (q0 is well-defined everywhere)
    program = """
        init: x = 0

        [] x < 5 -> x = x + 1

        rank(q0):
            [] x >= 0 && x <= 5 -> 5 - x

        rank(q1):
            [] x >= 0 && x <= 3 -> 3 - x

        automaton_init: q0

        trans(q0, q1): x >= 2
        trans(q1, q0): x < 3
    """

    gc_file = tmp_path / "test_init_subset.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    try:
        sys.stdout = StringIO()
        result = main([str(gc_file), "--bounds", "x:0:5"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data = json.loads(output)

    # B_init should be empty because only q0 is in Q_0, and q0 is well-defined everywhere
    assert len(data["embeddings"]["E_init"]) == 0

    # Now test with both states in Q_0
    program_both = program.replace("automaton_init: q0", "automaton_init: q0, q1")

    gc_file2 = tmp_path / "test_init_both.gc"
    gc_file2.write_text(program_both)

    old_stdout = sys.stdout
    try:
        sys.stdout = StringIO()
        result = main([str(gc_file2), "--bounds", "x:0:5"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data_both = json.loads(output)

    # B_init should contain x=4 and x=5 because q1 is undefined there
    assert len(data_both["embeddings"]["E_init"]) == 2


def test_underspecified_init_condition(tmp_path):
    """Test that init conditions can be underspecified (not mention all variables)."""
    # Init only specifies x=0, but program uses both x and y
    # S0 should contain all states where x=0 regardless of y value
    program = """
        init: x = 0

        [] x < 5 && y < 5 -> x = x + 1; y = y + 1

        rank(q0):
            [] x >= 0 && y >= 0 -> 10 - x - y

        automaton_init: q0

        trans(q0, q0): x < 5
    """

    gc_file = tmp_path / "test_underspec.gc"
    gc_file.write_text(program)

    old_stdout = sys.stdout
    try:
        sys.stdout = StringIO()
        result = main([str(gc_file), "--bounds", "x:0:2", "y:0:2"])
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert result == 0

    data = json.loads(output)

    # With x:0:2 and y:0:2, there are 9 total states
    # S0 should have 3 states: (x=0, y=0), (x=0, y=1), (x=0, y=2)
    assert data["metadata"]["num_states_enumerated"] == 9
    assert data["metadata"]["set_sizes"]["S0"] == 3

    # Verify S0 embeddings correspond to states with x=0
    # In state space with variables [x, y], states are indexed as:
    # 0:(0,0), 1:(0,1), 2:(0,2), 3:(1,0), 4:(1,1), 5:(1,2), 6:(2,0), 7:(2,1), 8:(2,2)
    # So S0 should be states 0, 1, 2 (all with x=0)
    expected_s0_embeddings = [0, 1, 2]
    assert data["embeddings"]["E_S0"] == expected_s0_embeddings
