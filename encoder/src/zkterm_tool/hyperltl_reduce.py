"""Reduce supported HyperLTL formulas to LTL over a self-composed ParseResult."""

from __future__ import annotations

import re
from dataclasses import replace
from itertools import product

from .ast_types import Assignment, BinOp, Comparison, CompOp, Expr, GuardedCommand, Neg, Num, TypeDef, Var
from .hyperltl_support import (
    UnsupportedHyperLtlError,
    require_hyperltl_parse_result_references,
    require_supported_hyperltl,
)
from .hyperltl_types import HyperFormula
from .parser import ParseResult


_ATOM_RE = re.compile(r"\{([^{}]*)\}", re.DOTALL)
_REF_RE = re.compile(r'^\s*"([^"]+)"_([A-Za-z_][A-Za-z_0-9]*)\s*$')
_CMP_RE = re.compile(r"(.+?)\s*(<->|!=|<=|>=|=|<|>)\s*(.+)")


class HyperLtlReductionError(ValueError):
    pass


def trace_var(name: str, trace: str) -> str:
    return f"{name}__{trace}"


def reduce_hyperltl_to_ltl(base: ParseResult, formula: HyperFormula) -> ParseResult:
    """Return a self-composed ParseResult with the HyperLTL body as an LTL spec.

    Supported semantics in this backend step:
      forall A. forall B. ... forall N. body

    Existential-only formulas are parsed and support-checked elsewhere, but
    they are not reduced here because the downstream verifier proves universal
    LTL satisfaction, not existential non-emptiness.
    """
    require_supported_hyperltl(formula)
    require_hyperltl_parse_result_references(formula, base)

    if any(q.kind != "forall" for q in formula.quantifiers):
        raise UnsupportedHyperLtlError(
            "HyperLTL reduction currently supports universal quantifier blocks only"
        )

    traces = list(formula.traces())
    if not traces:
        raise HyperLtlReductionError("cannot reduce HyperLTL without traces")

    reducer = _HyperReducer(base, traces)
    ltl_formula = reducer.reduce_body(formula.body)
    product_model = self_compose_parse_result(base, traces)
    product_model.aps.update(reducer.aps)
    product_model.ltl_formula = ltl_formula
    return product_model


def self_compose_parse_result(base: ParseResult, traces: list[str]) -> ParseResult:
    types: dict[str, TypeDef] = {}
    for trace in traces:
        for name, type_def in base.types.items():
            types[trace_var(name, trace)] = TypeDef(
                variable=trace_var(type_def.variable, trace),
                min_value=type_def.min_value,
                max_value=type_def.max_value,
            )

    init_condition: list[Comparison] = []
    if base.init_condition:
        for trace in traces:
            init_condition.extend(_rename_comparisons(base.init_condition, trace))

    commands: list[GuardedCommand] = []
    if base.commands:
        for combo in product(base.commands, repeat=len(traces)):
            guards: list[Comparison] = []
            assignments: list[Assignment] = []
            havoc: set[str] = set()
            for trace, command in zip(traces, combo):
                guards.extend(_rename_comparisons(command.guards, trace))
                assignments.extend(
                    Assignment(trace_var(assignment.var, trace), _rename_expr(assignment.expr, trace))
                    for assignment in command.assignments
                )
                havoc.update(trace_var(name, trace) for name in command.havoc)
            commands.append(GuardedCommand(guards=guards, assignments=assignments, havoc=frozenset(havoc)))

    return ParseResult(
        constants=dict(base.constants),
        types=types,
        init_condition=init_condition or None,
        commands=commands,
        ranking_functions={},
        automaton_transitions=[],
        automaton_initial_states=None,
        aps={},
        ltl_formula=None,
        observable_symbols=set(types),
        observable_definitions={},
    )


class _HyperReducer:
    def __init__(self, base: ParseResult, traces: list[str]):
        self.base = base
        self.traces = set(traces)
        self.aps: dict[str, list[Comparison]] = {}
        self._ap_index = 0

    def reduce_body(self, body: str) -> str:
        parts: list[str] = []
        pos = 0
        for match in _ATOM_RE.finditer(body):
            parts.append(body[pos:match.start()])
            parts.append(self._reduce_atom(match.group(1)))
            pos = match.end()
        parts.append(body[pos:])
        return "".join(parts).strip()

    def _reduce_atom(self, raw: str) -> str:
        raw = _strip_wrapping_parens(" ".join(raw.split()))

        cmp_match = _CMP_RE.fullmatch(raw)
        if cmp_match:
            left_raw, op, right_raw = cmp_match.groups()
            if op == "<->":
                left = self._reduce_boolean_operand(left_raw)
                right = self._reduce_boolean_operand(right_raw)
                return f"(({left}) <-> ({right}))"

            left = self._operand_to_expr(left_raw)
            right = self._operand_to_expr(right_raw)
            if op == "!=":
                ap = self._new_ap([Comparison(left, right, CompOp.EQ)])
                return f"!{ap}"
            ap = self._new_ap([Comparison(left, right, _to_comp_op(op))])
            return ap

        return self._reduce_boolean_operand(raw)

    def _reduce_boolean_operand(self, raw: str) -> str:
        name, trace = self._parse_ref(raw)
        if name in self.base.observable_definitions:
            return self._dnf_to_formula(self.base.observable_definitions[name], trace)
        if name in self.base.aps:
            return self._conjunction_to_ap(self.base.aps[name], trace)
        return self._new_ap([Comparison(Var(trace_var(name, trace)), Num(1), CompOp.EQ)])

    def _operand_to_expr(self, raw: str) -> Expr:
        raw = raw.strip()
        ref = _REF_RE.fullmatch(raw)
        if ref:
            name, trace = ref.groups()
            if trace not in self.traces:
                raise HyperLtlReductionError(f"undeclared trace in atom operand: {trace}")
            if name in self.base.observable_definitions or name in self.base.aps:
                raise HyperLtlReductionError(
                    f"observable '{name}' is boolean; use it as an atom, not as a comparison operand"
                )
            return Var(trace_var(name, trace))
        if raw.upper() == "TRUE":
            return Num(1)
        if raw.upper() == "FALSE":
            return Num(0)
        if re.fullmatch(r"-?[0-9]+", raw):
            return Num(int(raw))
        raise HyperLtlReductionError(f"unsupported HyperLTL atom operand: {raw}")

    def _parse_ref(self, raw: str) -> tuple[str, str]:
        ref = _REF_RE.fullmatch(raw)
        if not ref:
            raise HyperLtlReductionError(f"expected trace-indexed atom reference, got: {raw}")
        name, trace = ref.groups()
        if trace not in self.traces:
            raise HyperLtlReductionError(f"undeclared trace in atom reference: {trace}")
        return name, trace

    def _dnf_to_formula(self, dnf: list[list[Comparison]], trace: str) -> str:
        if not dnf:
            return "false"
        disjuncts = [self._conjunction_to_ap(conjunction, trace) for conjunction in dnf]
        if len(disjuncts) == 1:
            return disjuncts[0]
        return "(" + " | ".join(disjuncts) + ")"

    def _conjunction_to_ap(self, comparisons: list[Comparison], trace: str) -> str:
        if not comparisons:
            return "true"
        return self._new_ap(_rename_comparisons(comparisons, trace))

    def _new_ap(self, comparisons: list[Comparison]) -> str:
        name = f"hq_ap_{self._ap_index}"
        self._ap_index += 1
        self.aps[name] = comparisons
        return name


def _rename_expr(expr: Expr, trace: str) -> Expr:
    if isinstance(expr, Var):
        return Var(trace_var(expr.name, trace))
    if isinstance(expr, Num):
        return expr
    if isinstance(expr, BinOp):
        return replace(expr, left=_rename_expr(expr.left, trace), right=_rename_expr(expr.right, trace))
    if isinstance(expr, Neg):
        return Neg(_rename_expr(expr.expr, trace))
    raise TypeError(f"unknown expression type: {type(expr)}")


def _rename_comparisons(comparisons: list[Comparison], trace: str) -> list[Comparison]:
    return [
        Comparison(
            left=_rename_expr(comparison.left, trace),
            right=_rename_expr(comparison.right, trace),
            op=comparison.op,
        )
        for comparison in comparisons
    ]


def _strip_wrapping_parens(text: str) -> str:
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        wraps = True
        for i, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and i != len(text) - 1:
                    wraps = False
                    break
        if not wraps:
            break
        text = text[1:-1].strip()
    return text


def _to_comp_op(op: str) -> CompOp:
    return {
        "=": CompOp.EQ,
        "<": CompOp.LT,
        "<=": CompOp.LE,
        ">": CompOp.GT,
        ">=": CompOp.GE,
    }[op]
