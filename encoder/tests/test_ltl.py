"""Tests for LTL -> Büchi automaton support (Spot / ltl2tgba).

The pure-Python parts (HOA parsing, boolean-label DNF, predicate negation, lowering) are tested
without invoking Spot, using fixtured HOA text. Tests that actually run `ltl2tgba` are guarded by
`requires_spot` and skipped when the binary is not installed.
"""

import shutil

import pytest

from zkterm_tool import (
    parse_with_constants, verify_termination,
    Comparison, CompOp, Var, Num,
    parse_label_to_dnf, negate_comparison, parse_hoa, lower_to_transitions,
    derive_automaton, resolve_automaton,
)
from zkterm_tool.ltl import find_ltl2tgba, run_ltl2tgba


def _spot_available() -> bool:
    if shutil.which("ltl2tgba"):
        return True
    try:
        find_ltl2tgba()
        return True
    except Exception:
        return False


requires_spot = pytest.mark.skipif(not _spot_available(), reason="Spot's ltl2tgba not installed")


# --------------------------------------------------------------------------------------
# Grammar / parser
# --------------------------------------------------------------------------------------

class TestParseApSpec:
    def test_ap_and_spec_parsed(self):
        result = parse_with_constants(
            'type x: 0..5\n'
            'ap a := x == 1\n'
            'ap b := x >= 3\n'
            'spec: "G (a -> F b)"\n'
        )
        assert result.ltl_formula == "G (a -> F b)"
        assert set(result.aps.keys()) == {"a", "b"}
        assert result.aps["a"] == [Comparison(Var("x"), Num(1), CompOp.EQ)]
        assert result.aps["b"] == [Comparison(Var("x"), Num(3), CompOp.GE)]
        # No resolution requested -> automaton stays empty, no subprocess spawned.
        assert result.automaton_transitions == []

    def test_constants_substituted_in_ap(self):
        result = parse_with_constants('const wait = 0\nap waiting := status == wait\n')
        assert result.aps["waiting"] == [Comparison(Var("status"), Num(0), CompOp.EQ)]

    def test_conjunctive_ap(self):
        result = parse_with_constants('ap p := x >= 0 && x <= 5\nspec: "G p"\n')
        assert len(result.aps["p"]) == 2

    def test_true_ap_rejected(self):
        with pytest.raises(Exception):
            parse_with_constants('ap p := true\n')

    def test_reserved_ap_names_rejected(self):
        # Spot constant-folds `true`/`false`, silently vacuizing the property.
        with pytest.raises(Exception, match="reserved"):
            parse_with_constants('ap true := x == 1\n')
        with pytest.raises(Exception, match="reserved"):
            parse_with_constants('ap false := x == 1\n')

    def test_duplicate_spec_rejected(self):
        with pytest.raises(Exception):
            parse_with_constants('spec: "G a"\nspec: "F a"\n')


# --------------------------------------------------------------------------------------
# Boolean label -> DNF
# --------------------------------------------------------------------------------------

def _norm(dnf):
    """Normalise a DNF (list of dicts) into a comparable set of frozensets."""
    return {frozenset(cube.items()) for cube in dnf}


class TestLabelToDnf:
    def test_true_false(self):
        assert parse_label_to_dnf("t") == [{}]
        assert parse_label_to_dnf("f") == []

    def test_single_atom(self):
        assert parse_label_to_dnf("0") == [{0: True}]
        assert parse_label_to_dnf("!1") == [{1: False}]

    def test_conjunction(self):
        assert _norm(parse_label_to_dnf("0&!1")) == {frozenset({(0, True), (1, False)})}

    def test_disjunction(self):
        assert _norm(parse_label_to_dnf("0|1")) == {
            frozenset({(0, True)}), frozenset({(1, True)})
        }

    def test_negation_of_conjunction(self):
        # !(0 & 1) = !0 | !1
        assert _norm(parse_label_to_dnf("!(0&1)")) == {
            frozenset({(0, False)}), frozenset({(1, False)})
        }

    def test_precedence_and_parens(self):
        # 0 | 1 & 2  ==  0 | (1 & 2)
        assert _norm(parse_label_to_dnf("0|1&2")) == {
            frozenset({(0, True)}), frozenset({(1, True), (2, True)})
        }
        assert _norm(parse_label_to_dnf("(0|1)&2")) == {
            frozenset({(0, True), (2, True)}), frozenset({(1, True), (2, True)})
        }

    def test_double_negation(self):
        assert parse_label_to_dnf("!!0") == [{0: True}]


# --------------------------------------------------------------------------------------
# Predicate negation
# --------------------------------------------------------------------------------------

class TestNegateComparison:
    def test_operator_flips(self):
        c = lambda op: Comparison(Var("x"), Num(3), op)
        assert negate_comparison(c(CompOp.LT)) == [c(CompOp.GE)]
        assert negate_comparison(c(CompOp.LE)) == [c(CompOp.GT)]
        assert negate_comparison(c(CompOp.GT)) == [c(CompOp.LE)]
        assert negate_comparison(c(CompOp.GE)) == [c(CompOp.LT)]

    def test_equality_splits_into_two_disjuncts(self):
        neg = negate_comparison(Comparison(Var("x"), Num(3), CompOp.EQ))
        assert neg == [
            Comparison(Var("x"), Num(3), CompOp.LT),
            Comparison(Var("x"), Num(3), CompOp.GT),
        ]


# --------------------------------------------------------------------------------------
# HOA parsing + lowering (no subprocess)
# --------------------------------------------------------------------------------------

# state-based single-set Büchi for !(G F !a) = F G a  (the exp-backoff shape).
FGA_HOA = """HOA: v1
name: "FGa"
States: 2
Start: 0
AP: 1 "waiting"
acc-name: Buchi
Acceptance: 1 Inf(0)
properties: trans-labels explicit-labels state-acc stutter-invariant
--BODY--
State: 0
[t] 0
[0] 1
State: 1 {0}
[0] 1
--END--
"""


class TestHoaParsing:
    def test_parse_hoa_basic(self):
        aut = parse_hoa(FGA_HOA)
        assert aut.start_states == [0]
        assert aut.ap_names == ["waiting"]
        assert aut.accepting_states == {1}
        assert not aut.all_accepting
        assert len(aut.edges) == 3
        # edge (1 -> 1) exists and is from an accepting source
        assert any(e.src == 1 and e.dst == 1 for e in aut.edges)

    def test_lower_matches_handwritten(self):
        aut = parse_hoa(FGA_HOA)
        aps = {"waiting": [Comparison(Var("status"), Num(0), CompOp.EQ)]}
        transitions, init = lower_to_transitions(aut, aps)
        init_set = set(init)
        assert init_set == {"q0"}

        rendered = {(t.from_state, t.to_state, t.is_fair,
                     tuple(str(g) for g in t.guards)) for t in transitions}
        assert rendered == {
            ("q0", "q0", False, ()),                       # [t] self-loop, regular
            ("q0", "q1", False, ("status = 0",)),          # source q0 not accepting
            ("q1", "q1", True, ("status = 0",)),           # source q1 accepting -> fair
        }

    def test_missing_ap_binding_raises(self):
        aut = parse_hoa(FGA_HOA)
        with pytest.raises(ValueError, match="atomic proposition"):
            lower_to_transitions(aut, aps={})  # no binding for "waiting"

    def test_equality_negation_expands_to_two_transitions(self):
        # Edge label !0 where AP0 is an equality -> two transitions (x<c OR x>c).
        hoa = """HOA: v1
States: 2
Start: 0
AP: 1 "a"
Acceptance: 1 Inf(0)
--BODY--
State: 0
[!0] 1
State: 1 {0}
[t] 1
--END--
"""
        aut = parse_hoa(hoa)
        aps = {"a": [Comparison(Var("x"), Num(1), CompOp.EQ)]}
        transitions, _ = lower_to_transitions(aut, aps)
        q0_edges = [t for t in transitions if t.from_state == "q0"]
        guards = {tuple(str(g) for g in t.guards) for t in q0_edges}
        assert guards == {("x < 1",), ("x > 1",)}


class TestHoaRobustness:
    def test_nonzero_start_state(self):
        # Spot's state numbering is not guaranteed to start the run at 0.
        hoa = """HOA: v1
States: 2
Start: 1
AP: 1 "a"
Acceptance: 1 Inf(0)
--BODY--
State: 0 {0}
[t] 0
State: 1
[0] 0
--END--
"""
        aut = parse_hoa(hoa)
        aps = {"a": [Comparison(Var("x"), Num(1), CompOp.EQ)]}
        _, init = lower_to_transitions(aut, aps)
        assert init == ["q1"]

    def test_cobuchi_acceptance_rejected(self):
        hoa = """HOA: v1
States: 1
Start: 0
AP: 0
Acceptance: 1 Fin(0)
--BODY--
State: 0 {0}
[t] 0
--END--
"""
        with pytest.raises(ValueError, match="Inf"):
            parse_hoa(hoa)

    def test_out_of_range_ap_index(self):
        hoa = """HOA: v1
States: 1
Start: 0
AP: 1 "a"
Acceptance: 1 Inf(0)
--BODY--
State: 0 {0}
[1] 0
--END--
"""
        aut = parse_hoa(hoa)
        aps = {"a": [Comparison(Var("x"), Num(1), CompOp.EQ)]}
        with pytest.raises(ValueError, match="AP index"):
            lower_to_transitions(aut, aps)

    def test_conjunctive_ap_negation_branches(self):
        # !p with p := x >= 0 && x <= 5 must branch into x < 0 OR x > 5.
        hoa = """HOA: v1
States: 1
Start: 0
AP: 1 "p"
Acceptance: 1 Inf(0)
--BODY--
State: 0 {0}
[!0] 0
--END--
"""
        aut = parse_hoa(hoa)
        aps = {"p": [Comparison(Var("x"), Num(0), CompOp.GE),
                     Comparison(Var("x"), Num(5), CompOp.LE)]}
        transitions, _ = lower_to_transitions(aut, aps)
        guards = {tuple(str(g) for g in t.guards) for t in transitions}
        assert guards == {("x < 0",), ("x > 5",)}

    def test_dnf_cube_cap(self):
        # Negating a 13-clause sum-of-products explodes to 2^13 cubes — must be refused, not built.
        label = "!(" + "|".join(f"({2 * i}&{2 * i + 1})" for i in range(13)) + ")"
        with pytest.raises(ValueError, match="cubes"):
            parse_label_to_dnf(label)


class TestMissingRankingGuard:
    def test_missing_rank_for_automaton_state_errors(self):
        # A state on an automaton transition without a rank(...) used to be skipped silently,
        # yielding a vacuous PASS. It must be a loud error.
        src = """
type x: 0..5
init: x = 0
[] x < 5 -> x = x + 1
rank(q0):
  [] x >= 0 && x <= 5 -> 5 - x
  [] x < 0 -> inf
  [] x > 5 -> inf
automaton_init: q0
trans!(q0, q1): x < 5
trans!(q1, q0): x < 5
"""
        result = parse_with_constants(src)
        with pytest.raises(ValueError, match="No ranking function for automaton state"):
            verify_termination(result)


# --------------------------------------------------------------------------------------
# Spot integration (skipped without ltl2tgba)
# --------------------------------------------------------------------------------------

@requires_spot
class TestSpotIntegration:
    def test_derive_exp_backoff_automaton(self):
        aps = {"waiting": [Comparison(Var("status"), Num(0), CompOp.EQ)]}
        transitions, init = derive_automaton("G F !waiting", aps)
        assert set(init) == {"q0"}
        rendered = {(t.from_state, t.to_state, t.is_fair,
                     tuple(str(g) for g in t.guards)) for t in transitions}
        assert rendered == {
            ("q0", "q0", False, ()),
            ("q0", "q1", False, ("status = 0",)),
            ("q1", "q1", True, ("status = 0",)),
        }

    def test_conflicting_spec_and_trans_rejected(self):
        result = parse_with_constants(
            'ap a := x == 1\n'
            'spec: "G F a"\n'
            'trans(q0, q0): x == 1\n'
        )
        with pytest.raises(ValueError, match="both"):
            resolve_automaton(result)

    def test_resolve_automaton_is_idempotent(self):
        result = parse_with_constants('ap a := x == 1\nspec: "G F a"\n', resolve_ltl=True)
        transitions = list(result.automaton_transitions)
        resolve_automaton(result)  # second call: no-op, not an error
        assert result.automaton_transitions == transitions

    def test_trivially_true_spec_rejected_with_hint(self):
        # !(G(a | !a)) is unsatisfiable -> empty automaton; the error must say the property
        # trivially holds instead of complaining about missing trans(...) declarations.
        result = parse_with_constants('ap a := x == 1\nspec: "G (a | !a)"\n')
        with pytest.raises(ValueError, match="trivially"):
            resolve_automaton(result)

    def test_invalid_formula_reports_stderr(self):
        with pytest.raises(ValueError, match="rejected"):
            run_ltl2tgba("G F (")

    def test_end_to_end_verify_matches_handwritten(self):
        """The LTL example must verify identically to the hand-written automaton example."""
        import pathlib
        examples = pathlib.Path(__file__).resolve().parents[1] / "examples"
        ltl_text = (examples / "exp_backoff_ltl.gc").read_text()
        hand_text = (examples / "exp_backoff_state_opt_small.gc").read_text()

        ltl_result = parse_with_constants(ltl_text, resolve_ltl=True)
        hand_result = parse_with_constants(hand_text)

        ltl_v = verify_termination(ltl_result)
        hand_v = verify_termination(hand_result)

        assert ltl_v.passed is True
        assert ltl_v.passed == hand_v.passed
        assert len(ltl_v.obligations) == len(hand_v.obligations)
