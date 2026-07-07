"""Tests for the KoAT .koat integer-transition-system importer + end-to-end termination synthesis."""

import importlib.util
import pathlib

import pytest

from zkterm_tool import import_koat, verify_termination, synthesize_rankings
from zkterm_tool.synth import synthesize_into, SynthesisError
from zkterm_tool.ast_types import CompOp


def _load_run_its():
    p = pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "run_its.py"
    spec = importlib.util.spec_from_file_location("run_its_mod", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


class TestInputVariableNotPartitioned:
    def test_havoc_input_var_excluded_from_partition(self):
        # B is a fresh (havoc'd) nondeterministic input with a guard split point; it must NOT become
        # a partition variable, so no finite ranking case should be guarded on B.
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(VAR A B)
(RULES
  l0(A) -> Com_1(l0(A - 1)) :|: A >= 1 && B >= 0
)
"""
        r = import_koat(src)
        assert "B" in r.commands[0].havoc
        rankings = synthesize_rankings(r)
        for rf in rankings.values():
            for case in rf.cases:
                if not case.is_infinity:
                    assert "B" not in case.get_variables()  # B never used as a partition dimension


class TestHarnessCategorizer:
    def _classify(self, tmp_path, name, text):
        run_its = _load_run_its()
        p = tmp_path / name
        p.write_text(text)
        return run_its._classify(str(p), max_mode_vars=2, max_regions=64, emit_dir=None)[0]

    def test_pass_bucket(self, tmp_path):
        src = ("(GOAL COMPLEXITY)\n(STARTTERM (FUNCTIONSYMBOLS l0))\n(VAR A)\n"
               "(RULES\n  l0(A) -> Com_1(l0(A - 1)) :|: A >= 1\n)\n")
        assert self._classify(tmp_path, "ok.koat", src) == "pass"

    def test_comn_bucket(self, tmp_path):
        src = ("(GOAL COMPLEXITY)\n(STARTTERM (FUNCTIONSYMBOLS l0))\n(VAR A)\n"
               "(RULES\n  l0(A) -> Com_2(l1(A), l2(A)) :|: A >= 0\n)\n")
        assert self._classify(tmp_path, "comn.koat", src) == "unsupported-comn"

    def test_nonlinear_bucket(self, tmp_path):
        src = ("(GOAL COMPLEXITY)\n(STARTTERM (FUNCTIONSYMBOLS l0))\n(VAR A B)\n"
               "(RULES\n  l0(A,B) -> Com_1(l0(A * B, B)) :|: A >= 1\n)\n")
        assert self._classify(tmp_path, "nonlin.koat", src) == "unsupported-nonlinear"
