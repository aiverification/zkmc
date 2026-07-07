"""Tests for the KoAT .koat integer-transition-system importer + end-to-end termination synthesis."""

import pathlib

import pytest

from zkterm_tool import import_koat, verify_termination
from zkterm_tool.synth import synthesize_into, SynthesisError
from zkterm_tool.ast_types import CompOp


SECT5_LEN = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(VAR A B)
(RULES
  l0(A,B) -> Com_1(l1(0,B))
  l1(A,B) -> Com_1(l1(A + 1,B - 1)) :|: B >= 1
  l1(A,B) -> Com_1(l2(A,B)) :|: 0 >= B
)
"""


class TestImporter:
    def test_locations_pc_and_commands(self):
        r = import_koat(SECT5_LEN)
        # 3 locations -> pc : 0..2
        assert "pc" in r.types
        assert (r.types["pc"].min_value, r.types["pc"].max_value) == (0, 2)
        # init at start location l0 (index 0)
        assert len(r.init_condition) == 1
        assert r.init_condition[0].op == CompOp.EQ
        # one guarded command per rule
        assert len(r.commands) == 3
        # data variables (A, B) are untyped (unbounded, symbolic path)
        assert "A" not in r.types and "B" not in r.types

    def test_termination_automaton(self):
        r = import_koat(SECT5_LEN)
        assert r.automaton_initial_states == ["q0"]
        assert len(r.automaton_transitions) == 1
        t = r.automaton_transitions[0]
        assert (t.from_state, t.to_state, t.is_fair, t.guards) == ("q0", "q0", True, [])

    def test_fresh_variable_is_havoced(self):
        # C is declared but not a location parameter -> nondeterministic input -> havoc'd,
        # and assigned into A (A' = C).
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(VAR A B C)
(RULES
  l0(A,B) -> Com_1(l0(C, B)) :|: B >= 1 && C >= 0
)
"""
        r = import_koat(src)
        cmd = r.commands[0]
        assert "C" in cmd.havoc
        assert any(a.var == "A" for a in cmd.assignments)  # A' = C

    def test_com_n_recursion_rejected(self):
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(VAR A)
(RULES
  l0(A) -> Com_2(l1(A), l2(A)) :|: A >= 0
)
"""
        with pytest.raises(ValueError, match="recursion|Com_n|successors"):
            import_koat(src)


class TestEndToEnd:
    def test_sect5_len_synthesizes_and_verifies(self):
        r = import_koat(SECT5_LEN)
        synthesize_into(r)  # symbolic, no type bounds
        v = verify_termination(r)
        assert v.passed is True
        assert len(v.obligations) > 0

    def test_real_example_files_import(self):
        examples = pathlib.Path(__file__).resolve().parents[1] / "examples" / "its"
        for f in examples.glob("*.koat"):
            r = import_koat(f.read_text())
            assert "pc" in r.types
            assert r.commands
            assert r.automaton_transitions[0].is_fair
