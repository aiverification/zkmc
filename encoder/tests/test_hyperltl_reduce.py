"""Tests for reducing supported HyperLTL to LTL over self-composed GC IR."""

import pytest

from zkterm_tool import (
    CompOp,
    Num,
    UnsupportedHyperLtlError,
    Var,
    import_smv,
    parse_hq,
    reduce_hyperltl_to_ltl,
    trace_var,
)


SIMPLE_SMV = """
MODULE main
VAR
  x : 0..1;
ASSIGN
  init(x) := 0;
  next(x) := x;
"""


def test_self_composes_model_and_reduces_equality_atom():
    base = import_smv(SIMPLE_SMV)
    formula = parse_hq('forall A. forall B. G({"x"_A = "x"_B})')

    result = reduce_hyperltl_to_ltl(base, formula)

    assert set(result.types) == {"x__A", "x__B"}
    assert [(c.left, c.op, c.right) for c in result.init_condition] == [
        (Var("x__A"), CompOp.EQ, Num(0)),
        (Var("x__B"), CompOp.EQ, Num(0)),
    ]
    assert len(result.commands) == 1
    assert [assignment.var for assignment in result.commands[0].assignments] == ["x__A", "x__B"]

    assert result.ltl_formula == "G(hq_ap_0)"
    assert result.aps["hq_ap_0"][0].left == Var("x__A")
    assert result.aps["hq_ap_0"][0].right == Var("x__B")


def test_not_equal_atom_becomes_negated_equality_ap():
    base = import_smv(SIMPLE_SMV)
    formula = parse_hq('forall A. forall B. G({"x"_A != "x"_B})')

    result = reduce_hyperltl_to_ltl(base, formula)

    assert result.ltl_formula == "G(!hq_ap_0)"
    assert result.aps["hq_ap_0"][0].op == CompOp.EQ


def test_boolean_smv_define_expands_to_ltl_disjunction():
    base = import_smv("""
MODULE main
VAR
  pc : 0..2;
DEFINE
  reached := (pc = 1) | (pc = 2);
ASSIGN
  init(pc) := 0;
  next(pc) := pc;
""")
    formula = parse_hq('forall A. G({"reached"_A})')

    result = reduce_hyperltl_to_ltl(base, formula)

    assert result.ltl_formula == "G((hq_ap_0 | hq_ap_1))"
    assert result.aps["hq_ap_0"] == [result.aps["hq_ap_0"][0]]
    assert result.aps["hq_ap_0"][0].left == Var("pc__A")
    assert result.aps["hq_ap_1"][0].left == Var("pc__A")


def test_trace_var_helper():
    assert trace_var("pc", "A") == "pc__A"


def test_rejects_existential_reduction_for_current_backend():
    base = import_smv(SIMPLE_SMV)
    formula = parse_hq('exists A. exists B. G({"x"_A = "x"_B})')

    with pytest.raises(UnsupportedHyperLtlError, match="universal"):
        reduce_hyperltl_to_ltl(base, formula)
