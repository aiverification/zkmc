"""Support checks for HyperLTL fragments accepted by future reduction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ast_types import BinOp, Comparison, Expr, Neg, Var
from .hyperltl_types import HyperFormula

if TYPE_CHECKING:
    from .parser import ParseResult


@dataclass(frozen=True)
class HyperLtlSupport:
    supported: bool
    reasons: tuple[str, ...]


class UnsupportedHyperLtlError(ValueError):
    pass


def has_quantifier_alternation(formula: HyperFormula) -> bool:
    kinds = formula.quantifier_kinds()
    if not kinds:
        return False
    return any(kind != kinds[0] for kind in kinds[1:])


def check_hyperltl_support(formula: HyperFormula) -> HyperLtlSupport:
    reasons: list[str] = []

    if not formula.quantifiers:
        reasons.append("missing HyperLTL quantifier prefix")

    traces = formula.traces()
    duplicate_traces = sorted({trace for trace in traces if traces.count(trace) > 1})
    if duplicate_traces:
        reasons.append(f"duplicate trace quantifier(s): {', '.join(duplicate_traces)}")

    if has_quantifier_alternation(formula):
        prefix = " ".join(str(q) for q in formula.quantifiers)
        reasons.append(f"quantifier alternation is unsupported: {prefix}")

    declared = set(traces)
    used = {
        ref.trace
        for atom in formula.atoms
        for ref in atom.references
    }
    undeclared = sorted(used - declared)
    if undeclared:
        reasons.append(f"atom references undeclared trace(s): {', '.join(undeclared)}")

    return HyperLtlSupport(supported=not reasons, reasons=tuple(reasons))


def check_hyperltl_model_references(
    formula: HyperFormula,
    model_symbols: set[str],
) -> HyperLtlSupport:
    used = {
        ref.variable
        for atom in formula.atoms
        for ref in atom.references
    }
    missing = sorted(used - model_symbols)
    reasons = (
        (f"atom references unknown SMV symbol(s): {', '.join(missing)}",)
        if missing else ()
    )
    return HyperLtlSupport(supported=not reasons, reasons=reasons)


def parse_result_symbols(result: ParseResult) -> set[str]:
    symbols: set[str] = set(result.observable_symbols)
    symbols.update(result.constants)
    symbols.update(result.types)
    symbols.update(result.aps)
    symbols.update(result.observable_definitions)

    def collect_expr(expr: Expr) -> None:
        if isinstance(expr, Var):
            symbols.add(expr.name)
        elif isinstance(expr, BinOp):
            collect_expr(expr.left)
            collect_expr(expr.right)
        elif isinstance(expr, Neg):
            collect_expr(expr.expr)

    def collect_comparisons(comparisons: list[Comparison]) -> None:
        for comparison in comparisons:
            collect_expr(comparison.left)
            collect_expr(comparison.right)

    if result.init_condition:
        collect_comparisons(result.init_condition)

    for command in result.commands:
        collect_comparisons(command.guards)
        symbols.update(command.havoc)
        for assignment in command.assignments:
            symbols.add(assignment.var)
            collect_expr(assignment.expr)

    for ranking in result.ranking_functions.values():
        for case in ranking.cases:
            collect_comparisons(case.guards)
            if case.expression is not None:
                collect_expr(case.expression)

    for transition in result.automaton_transitions:
        collect_comparisons(transition.guards)

    for comparisons in result.aps.values():
        collect_comparisons(comparisons)

    for dnf in result.observable_definitions.values():
        for comparisons in dnf:
            collect_comparisons(comparisons)

    return symbols


def check_hyperltl_parse_result_references(
    formula: HyperFormula,
    result: ParseResult,
) -> HyperLtlSupport:
    return check_hyperltl_model_references(formula, parse_result_symbols(result))


def require_hyperltl_model_references(
    formula: HyperFormula,
    model_symbols: set[str],
) -> None:
    support = check_hyperltl_model_references(formula, model_symbols)
    if not support.supported:
        raise UnsupportedHyperLtlError("; ".join(support.reasons))


def require_hyperltl_parse_result_references(
    formula: HyperFormula,
    result: ParseResult,
) -> None:
    support = check_hyperltl_parse_result_references(formula, result)
    if not support.supported:
        raise UnsupportedHyperLtlError("; ".join(support.reasons))


def require_supported_hyperltl(formula: HyperFormula) -> None:
    support = check_hyperltl_support(formula)
    if not support.supported:
        raise UnsupportedHyperLtlError("; ".join(support.reasons))
