"""Tests for parser-only HyperLTL .hq import and support checks."""

import pytest

from zkterm_tool import (
    UnsupportedHyperLtlError,
    check_hyperltl_model_references,
    check_hyperltl_parse_result_references,
    check_hyperltl_support,
    import_smv,
    has_quantifier_alternation,
    parse_hq,
    parse_hq_file,
    require_supported_hyperltl,
)


SUPPORTED_HQ = """
forall A. forall B.
G
(
    (
        {"secret"_A != "secret"_B}
        &
        {"pc"_A = "pc"_B}
    )
    ->
    (
        {"obs"_A = "obs"_B}
    )
)
"""


def test_parse_quantifiers_body_and_atoms():
    formula = parse_hq(SUPPORTED_HQ)

    assert [q.kind for q in formula.quantifiers] == ["forall", "forall"]
    assert [q.trace for q in formula.quantifiers] == ["A", "B"]
    assert formula.body.startswith("G")
    assert len(formula.atoms) == 3

    first = formula.atoms[0]
    assert first.raw == '"secret"_A != "secret"_B'
    assert [(ref.variable, ref.trace) for ref in first.references] == [
        ("secret", "A"),
        ("secret", "B"),
    ]


def test_parse_hq_file(tmp_path):
    path = tmp_path / "property.hq"
    path.write_text(SUPPORTED_HQ)

    formula = parse_hq_file(str(path))

    assert formula.traces() == ("A", "B")


def test_supported_uniform_quantifiers():
    formula = parse_hq(SUPPORTED_HQ)
    support = check_hyperltl_support(formula)

    assert support.supported is True
    assert support.reasons == ()
    require_supported_hyperltl(formula)


def test_reject_quantifier_alternation():
    formula = parse_hq('forall A. exists B. G({"x"_A = "x"_B})')

    assert has_quantifier_alternation(formula) is True
    support = check_hyperltl_support(formula)
    assert support.supported is False
    assert "quantifier alternation" in support.reasons[0]

    with pytest.raises(UnsupportedHyperLtlError, match="quantifier alternation"):
        require_supported_hyperltl(formula)


def test_reject_undeclared_trace_reference():
    formula = parse_hq('forall A. G({"x"_A = "x"_B})')

    support = check_hyperltl_support(formula)

    assert support.supported is False
    assert "undeclared trace" in support.reasons[0]


def test_reject_missing_quantifier_prefix():
    formula = parse_hq('G({"x"_A = "x"_A})')

    support = check_hyperltl_support(formula)

    assert support.supported is False
    assert "missing HyperLTL quantifier prefix" in support.reasons[0]


def test_reject_duplicate_trace_quantifier():
    formula = parse_hq('forall A. forall A. G({"x"_A = "x"_A})')

    support = check_hyperltl_support(formula)

    assert support.supported is False
    assert "duplicate trace" in support.reasons[0]


def test_check_model_symbol_references():
    formula = parse_hq('forall A. forall B. G({"x"_A = "y"_B})')

    support = check_hyperltl_model_references(formula, {"x"})

    assert support.supported is False
    assert "unknown SMV symbol" in support.reasons[0]


def test_check_parse_result_references_include_smv_defines():
    result = import_smv("""
MODULE main
VAR
  x : 0..1;
DEFINE
  obs := x = 1;
ASSIGN
  init(x) := 0;
  next(x) := x;
""")
    formula = parse_hq('forall A. forall B. G({"obs"_A = "obs"_B})')

    support = check_hyperltl_parse_result_references(formula, result)

    assert support.supported is True
