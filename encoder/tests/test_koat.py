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

    def test_exponentiation_rejected_as_nonlinear(self):
        # `A^2` must fail loudly: with `^` in identifiers it would silently become an opaque
        # rigid variable and the imported system would be the wrong one.
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(VAR A)
(RULES
  l0(A) -> Com_1(l0(A + 1)) :|: A^2 >= A
)
"""
        with pytest.raises(ValueError, match="on-linear"):
            import_koat(src)

    def test_undeclared_temp_is_havoced(self):
        # B is neither a formal nor declared in VAR: KoAT chooses it fresh per application, so it
        # must be havoc'd — treating it as rigid would prove termination of the wrong system.
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(VAR A)
(RULES
  l0(A) -> Com_1(l0(B)) :|: B > A
)
"""
        r = import_koat(src)
        assert "B" in r.commands[0].havoc

    def test_bare_rhs_without_com(self):
        # Undirected Lommen-style rules `l -> l'` (no Com_1 wrapper).
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(VAR A)
(RULES
  l0(A) -> l0(A - 1) :|: A >= 1
)
"""
        r = import_koat(src)
        assert len(r.commands) == 1

    def test_com_arity_mismatch_rejected(self):
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(VAR A)
(RULES
  l0(A) -> Com_2(l1(A)) :|: A >= 0
)
"""
        with pytest.raises(ValueError, match="malformed"):
            import_koat(src)

    def test_duplicate_formals_rejected(self):
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS f))
(VAR A)
(RULES
  f(A,A) -> Com_1(f(A - 1, A + 2)) :|: A >= 1
)
"""
        with pytest.raises(ValueError, match="Duplicate"):
            import_koat(src)

    def test_multiple_start_symbols_rejected(self):
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0 l9))
(VAR A)
(RULES
  l0(A) -> Com_1(l9(A - 1)) :|: A >= 1
)
"""
        with pytest.raises(ValueError, match="start symbols"):
            import_koat(src)

    def test_var_decl_optional(self):
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(RULES
  l0(A) -> Com_1(l0(A - 1)) :|: A >= 1
)
"""
        r = import_koat(src)
        assert len(r.commands) == 1

    def test_pc_collision_renamed(self):
        # A program variable named `pc` forces the injected counter onto `_pc`.
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(VAR A pc)
(RULES
  l0(A) -> Com_1(l0(A - 1)) :|: A >= 1
)
"""
        r = import_koat(src)
        assert list(r.types) == ["_pc"]

    def test_negative_literals_and_alt_conjunction(self):
        src = """(GOAL COMPLEXITY)
(STARTTERM (FUNCTIONSYMBOLS l0))
(VAR A B)
(RULES
  l0(A,B) -> Com_1(l0(A - 1, -1)) :|: A >= -5 /\\ B = 0
)
"""
        r = import_koat(src)
        assert len(r.commands[0].guards) == 3  # pc == 0, A >= -5, B = 0


class TestEndToEnd:
    def test_sect5_len_synthesizes_and_verifies(self):
        r = import_koat(SECT5_LEN)
        synthesize_into(r)  # symbolic, no type bounds
        v = verify_termination(r)
        assert v.passed is True
        assert len(v.obligations) > 0

    def test_real_example_files_import(self):
        examples = pathlib.Path(__file__).resolve().parents[1] / "examples" / "its"
        files = sorted(examples.glob("*.koat"))
        assert files, "no example .koat files found — glob would make this test vacuous"
        for f in files:
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

    def test_exponent_bucket(self, tmp_path):
        # `^` is rejected at import; it must land in the nonlinear bucket, not "pass" mangled.
        src = ("(GOAL COMPLEXITY)\n(STARTTERM (FUNCTIONSYMBOLS l0))\n(VAR A)\n"
               "(RULES\n  l0(A) -> Com_1(l0(A + 1)) :|: A^2 >= A\n)\n")
        assert self._classify(tmp_path, "exp.koat", src) == "unsupported-nonlinear"

    def test_pc_var_still_runs(self, tmp_path):
        # A file declaring `pc` must run (counter renamed to _pc), not land in "error".
        src = ("(GOAL COMPLEXITY)\n(STARTTERM (FUNCTIONSYMBOLS l0))\n(VAR A pc)\n"
               "(RULES\n  l0(A) -> Com_1(l0(A - 1)) :|: A >= 1\n)\n")
        assert self._classify(tmp_path, "pcvar.koat", src) == "pass"

    def test_progress_milestones(self):
        run_its = _load_run_its()
        reported, printed = 0, []
        for done in [10, 25, 25, 25, 26, 49, 51, 75, 75, 100]:
            if run_its._crossed_milestone(done, reported):
                printed.append(done)
                reported = done
        # Once per 25-bucket: no repeats while stalled at 25/75, no misses when 49->51 skips 50.
        assert printed == [25, 51, 75, 100]
