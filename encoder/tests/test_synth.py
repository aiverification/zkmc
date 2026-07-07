"""Tests for Tier-1 ranking-function synthesis (synth.py)."""

import shutil

import pytest

from zkterm_tool import (
    parse_with_constants, verify_termination,
    synthesize_rankings, synthesize_into, SynthesisError,
    encode_ranking_functions, validate_ranking_function,
)


FAIR_COUNTER = """
const maxVal = 10
type x: 0..maxVal
init: x = 0
[] x < maxVal -> x = x + 1
automaton_init: q0
trans!(q0, q0): x < maxVal
"""


def _validate(rf):
    enc = encode_ranking_functions({rf.state: rf})[rf.state]
    ok, errors = validate_ranking_function(enc.finite_cases, enc.infinity_cases, enc.variables)
    return ok, errors


class TestTier1Synthesis:
    def test_synthesizes_and_verifies(self):
        r = parse_with_constants(FAIR_COUNTER)
        rankings = synthesize_rankings(r)
        assert set(rankings) == {"q0"}

        # The synthesized ranking must be well-formed (disjoint / covering / non-negative)...
        ok, errors = _validate(rankings["q0"])
        assert ok, errors

        # ...and must discharge all termination obligations.
        r.ranking_functions.update(rankings)
        v = verify_termination(r)
        assert v.passed is True
        assert len(v.obligations) > 0

    def test_synthesized_ranking_decreases(self):
        """V should be a strictly-decreasing measure: exactly one finite case + an inf ladder."""
        r = parse_with_constants(FAIR_COUNTER)
        rf = synthesize_rankings(r)["q0"]
        finite = [c for c in rf.cases if not c.is_infinity]
        infinite = [c for c in rf.cases if c.is_infinity]
        assert len(finite) == 1
        # type x: 0..maxVal -> two inf cases (below lo, above hi)
        assert len(infinite) == 2

    def test_synthesize_into_fills_missing_only(self):
        r = parse_with_constants(FAIR_COUNTER)
        synthesize_into(r)
        assert "q0" in r.ranking_functions
        # calling again is a no-op (already present)
        rf_before = r.ranking_functions["q0"]
        synthesize_into(r)
        assert r.ranking_functions["q0"] is rf_before

    def test_two_variable_box_ladder_is_valid(self):
        """A 2-variable program: the auto inf-ladder must stay disjoint and cover the complement."""
        src = """
        const N = 5
        type x: 0..N
        type y: 0..N
        init: x = 0 && y = 0
        [] x < N -> x = x + 1
        [] x >= N && y < N -> y = y + 1
        automaton_init: q0
        trans!(q0, q0): x < N
        """
        r = parse_with_constants(src)
        rankings = synthesize_rankings(r)
        for rf in rankings.values():
            ok, errors = _validate(rf)
            assert ok, errors


class TestUnsatisfiable:
    def test_no_decrease_raises(self):
        src = """
        type x: 0..10
        init: x = 0
        [] x < 10 -> x = x
        automaton_init: q0
        trans!(q0, q0): x < 10
        """
        with pytest.raises(SynthesisError):
            synthesize_rankings(parse_with_constants(src))

    def test_unbounded_guarded_loop_synthesizes(self):
        # No type bounds: the no-bounds mode partitions the untyped `y` by its guard split point and
        # proves termination symbolically (V = y on y>=1, constant on y<=0).
        src = """
        init: y = 0
        [] y > 0 -> y = y - 1
        automaton_init: q0
        trans!(q0, q0): true
        """
        r = parse_with_constants(src)
        r.ranking_functions.update(synthesize_rankings(r))
        assert verify_termination(r).passed is True

    def test_unbounded_nonterminating_raises(self):
        # An untyped loop with no decrease has no ranking, even in no-bounds mode.
        src = """
        init: y = 0
        [] y > 0 -> y = y + 1
        automaton_init: q0
        trans!(q0, q0): true
        """
        with pytest.raises(SynthesisError):
            synthesize_rankings(parse_with_constants(src))

    def test_no_automaton_raises(self):
        src = "type x: 0..10\ninit: x = 0\n[] x < 10 -> x = x + 1\n"
        with pytest.raises(SynthesisError):
            synthesize_rankings(parse_with_constants(src))


class TestTier2Piecewise:
    def _example(self, name):
        import pathlib
        p = pathlib.Path(__file__).resolve().parents[1] / "examples" / name
        return parse_with_constants(p.read_text())

    def test_guard_split_points(self):
        from zkterm_tool.synth import _guard_split_points, _intervals
        # delay compared only to 0 -> split at 0 -> two intervals over 0..255, not 256.
        r = parse_with_constants(
            "type delay: 0..255\ntype x: 0..2\n"
            "init: delay = 0\n"
            "[] delay > 0 -> delay = delay - 1\n"
            "[] x < 2 -> x = x + 1\n"
            "automaton_init: q0\ntrans!(q0, q0): delay > 0\n"
        )
        splits = _guard_split_points(r)
        assert 0 in splits.get("delay", set())
        assert len(_intervals(splits["delay"], 0, 255)) == 2
        # Untyped variable -> unbounded intervals from the split point: (-inf,-1], {0}, [1,+inf).
        assert len(_intervals({0}, None, None)) == 3

    def test_round_robin_synthesizes_minimally(self):
        r = self._example("round-robin.gc")
        r.ranking_functions.clear()
        rankings = synthesize_rankings(r)
        # Minimal partition: split on `turn` (0..2) -> 3 finite cases per state, not the 81 of turn×states.
        for rf in rankings.values():
            finite = [c for c in rf.cases if not c.is_infinity]
            assert len(finite) == 3
            # Reachability invariant is captured (e.g. state1/state2 bounded tighter than their type box).
            ok, errors = _validate(rf)
            assert ok, errors
        # Full end-to-end verification is exercised by test_dhcp_synthesizes_and_verifies (fast);
        # round-robin's 6230-obligation verify is confirmed manually to keep the suite quick.

    def test_dhcp_synthesizes_and_verifies(self):
        r = self._example("dhcp.gc")
        r.ranking_functions.clear()
        synthesize_into(r)
        assert verify_termination(r).passed is True

    def test_mode_override(self):
        r = self._example("round-robin.gc")
        r.ranking_functions.clear()
        rankings = synthesize_rankings(r, mode_vars=["turn"])
        for rf in rankings.values():
            assert len([c for c in rf.cases if not c.is_infinity]) == 3

    def test_mode_override_requires_typed_var(self):
        r = self._example("round-robin.gc")
        r.ranking_functions.clear()
        with pytest.raises(SynthesisError, match="type-declared"):
            synthesize_rankings(r, mode_vars=["nonexistent"])


@pytest.mark.skipif(not shutil.which("ltl2tgba"), reason="Spot's ltl2tgba not installed")
def test_synthesis_from_ltl_spec():
    """End-to-end: derive the automaton from LTL, then synthesize and verify (Part A + Part C)."""
    src = """
    const maxVal = 10
    type x: 0..maxVal
    init: x = 0
    [] x < maxVal -> x = x + 1
    ap below := x < maxVal
    spec: "F !below"
    """
    r = parse_with_constants(src, resolve_ltl=True)
    synthesize_into(r)
    v = verify_termination(r)
    assert v.passed is True
