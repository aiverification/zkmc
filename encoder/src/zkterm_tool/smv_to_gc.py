"""Lower parsed SMV models into the guarded-command ParseResult IR."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

from .ast_types import Assignment, BinOp, CompOp, Comparison, Expr, GuardedCommand, Num, TypeDef, Var
from .parser import ParseResult
from .smv_parser import parse_smv
from .smv_types import (
    SmvAssignment,
    SmvBinary,
    SmvBool,
    SmvBooleanType,
    SmvCase,
    SmvDefine,
    SmvEnumType,
    SmvExpr,
    SmvInt,
    SmvModel,
    SmvName,
    SmvRangeType,
    SmvSet,
    SmvUnary,
)


@dataclass(frozen=True)
class _ValueAlternative:
    guards: tuple[Comparison, ...]
    value: Expr


class _SmvLowerer:
    def __init__(self, model: SmvModel):
        self.model = model
        self.variables = model.variable_map()
        self.defines = model.define_map()
        self.enum_values = self._build_enum_value_map()
        self._types_cache: dict[str, TypeDef] | None = None
        self._observable_definitions_cache: dict[str, list[list[Comparison]]] | None = None

    def lower(self) -> ParseResult:
        current_assignments = [a for a in self.model.assignments if a.kind == "current"]
        if current_assignments:
            names = ", ".join(a.target for a in current_assignments)
            raise ValueError(f"Current-state ASSIGN entries are not supported yet: {names}")

        return ParseResult(
            constants={},
            types=self._lower_types(),
            init_condition=self._lower_init(),
            commands=self._lower_next_commands(),
            ranking_functions={},
            automaton_transitions=[],
            automaton_initial_states=None,
            aps={},
            ltl_formula=None,
            observable_symbols=set(self.variables) | set(self._lower_observable_definitions()),
            observable_definitions=self._lower_observable_definitions(),
        )

    def _build_enum_value_map(self) -> dict[str, int]:
        result: dict[str, int] = {"FALSE": 0, "TRUE": 1}
        for smv_type in self.variables.values():
            if not isinstance(smv_type, SmvEnumType):
                continue
            for index, value in enumerate(smv_type.values):
                if value in result and result[value] != index:
                    raise ValueError(
                        f"Enum literal '{value}' has conflicting numeric encodings: "
                        f"{result[value]} and {index}"
                    )
                result[value] = index
        return result

    def _lower_types(self) -> dict[str, TypeDef]:
        if self._types_cache is not None:
            return self._types_cache

        types: dict[str, TypeDef] = {}
        for name, smv_type in self.variables.items():
            if isinstance(smv_type, SmvBooleanType):
                types[name] = TypeDef(name, 0, 1)
            elif isinstance(smv_type, SmvRangeType):
                types[name] = TypeDef(name, smv_type.min_value, smv_type.max_value)
            elif isinstance(smv_type, SmvEnumType):
                types[name] = TypeDef(name, 0, len(smv_type.values) - 1)
            else:
                raise ValueError(f"Unsupported SMV type for {name}: {smv_type}")
        self._types_cache = types
        return types

    def _lower_observable_definitions(self) -> dict[str, list[list[Comparison]]]:
        if self._observable_definitions_cache is not None:
            return self._observable_definitions_cache

        definitions: dict[str, list[list[Comparison]]] = {}
        for name, expr in self.defines.items():
            try:
                definitions[name] = self._guard_dnf(expr)
            except ValueError:
                continue
        self._observable_definitions_cache = definitions
        return definitions

    def _lower_init(self) -> list[Comparison] | None:
        init_guards: list[Comparison] = []
        for assignment in self.model.init_assignments():
            alternatives = self._value_alternatives(assignment.expr)
            if len(alternatives) == 1 and not alternatives[0].guards:
                init_guards.append(Comparison(Var(assignment.target), alternatives[0].value, CompOp.EQ))
                continue

            values = {alt.value for alt in alternatives if not alt.guards}
            if len(values) == len(alternatives) and self._is_full_domain(assignment.target, values):
                continue

            raise ValueError(
                f"init({assignment.target}) has nondeterministic or guarded values that cannot "
                "be represented as a conjunctive GC init condition yet"
            )
        return init_guards or None

    def _is_full_domain(self, target: str, values: set[Expr]) -> bool:
        if not all(isinstance(value, Num) for value in values):
            return False
        numeric_values = {value.value for value in values if isinstance(value, Num)}
        type_def = self._lower_types()[target]
        return numeric_values == set(range(type_def.min_value, type_def.max_value + 1))

    def _lower_next_commands(self) -> list[GuardedCommand]:
        next_assignments = self.model.next_assignments()
        assigned_targets = {assignment.target for assignment in next_assignments}
        missing_targets = frozenset(set(self.variables) - assigned_targets)

        if not next_assignments:
            return [GuardedCommand(guards=[], assignments=[], havoc=frozenset(self.variables))]

        alternatives_by_target = [
            (assignment.target, self._value_alternatives(assignment.expr))
            for assignment in next_assignments
        ]

        commands: list[GuardedCommand] = []
        for chosen in product(*(alts for _, alts in alternatives_by_target)):
            guards: list[Comparison] = []
            assignments: list[Assignment] = []
            consistent = True

            for (target, _), alternative in zip(alternatives_by_target, chosen):
                guards.extend(alternative.guards)
                assignments.append(Assignment(target, alternative.value))
                if not self._is_consistent(guards):
                    consistent = False
                    break

            if consistent:
                commands.append(
                    GuardedCommand(
                        guards=guards,
                        assignments=assignments,
                        havoc=missing_targets,
                    )
                )

        return commands

    def _value_alternatives(self, expr: SmvExpr) -> list[_ValueAlternative]:
        if isinstance(expr, SmvSet):
            return [
                _ValueAlternative(guards=(), value=self._lower_value(value))
                for value in expr.values
            ]

        if isinstance(expr, SmvCase):
            alternatives: list[_ValueAlternative] = []
            previous_guards: list[SmvExpr] = []
            for arm in expr.arms:
                dnf = self._guard_dnf(arm.guard)
                for previous in previous_guards:
                    dnf = self._dnf_and(dnf, self._guard_not_dnf(previous))
                for guards in dnf:
                    for value_alt in self._value_alternatives(arm.value):
                        combined = list(guards) + list(value_alt.guards)
                        if self._is_consistent(combined):
                            alternatives.append(
                                _ValueAlternative(
                                    guards=tuple(combined),
                                    value=value_alt.value,
                                )
                            )
                previous_guards.append(arm.guard)
            return alternatives

        return [_ValueAlternative(guards=(), value=self._lower_value(expr))]

    def _lower_value(self, expr: SmvExpr) -> Expr:
        expr = self._resolve_define_expr(expr)

        if isinstance(expr, SmvInt):
            return Num(expr.value)
        if isinstance(expr, SmvBool):
            return Num(1 if expr.value else 0)
        if isinstance(expr, SmvName):
            if expr.name in self.variables:
                return Var(expr.name)
            if expr.name in self.enum_values:
                return Num(self.enum_values[expr.name])
            raise ValueError(f"Unknown SMV symbol in value expression: {expr.name}")
        if isinstance(expr, SmvUnary) and expr.op == "-":
            lowered = self._lower_value(expr.expr)
            if isinstance(lowered, Num):
                return Num(-lowered.value)
            return BinOp("*", Num(-1), lowered)
        if isinstance(expr, SmvBinary) and expr.op in {"+", "-", "*"}:
            return BinOp(expr.op, self._lower_value(expr.left), self._lower_value(expr.right))

        raise ValueError(f"Cannot lower SMV expression as a GC value: {expr}")

    def _resolve_define_expr(self, expr: SmvExpr, seen: frozenset[str] = frozenset()) -> SmvExpr:
        if isinstance(expr, SmvName) and expr.name in self.defines:
            if expr.name in seen:
                raise ValueError(f"Recursive SMV DEFINE reference involving {expr.name}")
            return self._resolve_define_expr(self.defines[expr.name], seen | {expr.name})
        return expr

    def _guard_dnf(self, expr: SmvExpr) -> list[list[Comparison]]:
        expr = self._resolve_define_expr(expr)

        if isinstance(expr, SmvBool):
            return [[]] if expr.value else []

        if isinstance(expr, SmvName):
            if expr.name in self.variables:
                return [[Comparison(Var(expr.name), Num(1), CompOp.EQ)]]
            if expr.name in self.enum_values:
                return [[]] if self.enum_values[expr.name] != 0 else []
            raise ValueError(f"Unknown SMV symbol in guard: {expr.name}")

        if isinstance(expr, SmvUnary) and expr.op == "!":
            return self._guard_not_dnf(expr.expr)

        if isinstance(expr, SmvBinary) and expr.op == "&":
            return self._dnf_and(self._guard_dnf(expr.left), self._guard_dnf(expr.right))

        if isinstance(expr, SmvBinary) and expr.op == "|":
            return self._dnf_or(self._guard_dnf(expr.left), self._guard_dnf(expr.right))

        if isinstance(expr, SmvBinary) and expr.op in {"=", "!=", "<", "<=", ">", ">="}:
            return self._comparison_dnf(expr.op, expr.left, expr.right)

        raise ValueError(f"Cannot lower SMV expression as a GC guard: {expr}")

    def _guard_not_dnf(self, expr: SmvExpr) -> list[list[Comparison]]:
        expr = self._resolve_define_expr(expr)

        if isinstance(expr, SmvBool):
            return [] if expr.value else [[]]

        if isinstance(expr, SmvName):
            if expr.name in self.variables:
                return [[Comparison(Var(expr.name), Num(0), CompOp.EQ)]]
            if expr.name in self.enum_values:
                return [] if self.enum_values[expr.name] != 0 else [[]]
            raise ValueError(f"Unknown SMV symbol in negated guard: {expr.name}")

        if isinstance(expr, SmvUnary) and expr.op == "!":
            return self._guard_dnf(expr.expr)

        if isinstance(expr, SmvBinary) and expr.op == "&":
            return self._dnf_or(self._guard_not_dnf(expr.left), self._guard_not_dnf(expr.right))

        if isinstance(expr, SmvBinary) and expr.op == "|":
            return self._dnf_and(self._guard_not_dnf(expr.left), self._guard_not_dnf(expr.right))

        if isinstance(expr, SmvBinary) and expr.op in {"=", "!=", "<", "<=", ">", ">="}:
            negated = {
                "=": "!=",
                "!=": "=",
                "<": ">=",
                "<=": ">",
                ">": "<=",
                ">=": "<",
            }[expr.op]
            return self._comparison_dnf(negated, expr.left, expr.right)

        raise ValueError(f"Cannot lower negated SMV guard: {expr}")

    def _comparison_dnf(self, op: str, left: SmvExpr, right: SmvExpr) -> list[list[Comparison]]:
        left_expr = self._lower_value(left)
        right_expr = self._lower_value(right)
        if op == "!=":
            return [
                [Comparison(left_expr, right_expr, CompOp.LT)],
                [Comparison(left_expr, right_expr, CompOp.GT)],
            ]
        return [[Comparison(left_expr, right_expr, self._comparison_op(op))]]

    def _comparison_op(self, op: str) -> CompOp:
        return {
            "=": CompOp.EQ,
            "<": CompOp.LT,
            "<=": CompOp.LE,
            ">": CompOp.GT,
            ">=": CompOp.GE,
        }[op]

    def _dnf_and(
        self,
        left: list[list[Comparison]],
        right: list[list[Comparison]],
    ) -> list[list[Comparison]]:
        result: list[list[Comparison]] = []
        for l_conj in left:
            for r_conj in right:
                combined = l_conj + r_conj
                if self._is_consistent(combined):
                    result.append(combined)
        return result

    def _dnf_or(
        self,
        left: list[list[Comparison]],
        right: list[list[Comparison]],
    ) -> list[list[Comparison]]:
        return left + right

    def _is_consistent(self, guards: list[Comparison]) -> bool:
        equalities: dict[str, int] = {}

        for guard in guards:
            if isinstance(guard.left, Var) and isinstance(guard.right, Num) and guard.op == CompOp.EQ:
                previous = equalities.get(guard.left.name)
                if previous is not None and previous != guard.right.value:
                    return False
                equalities[guard.left.name] = guard.right.value

        for guard in guards:
            if not isinstance(guard.left, Var) or not isinstance(guard.right, Num):
                continue
            type_def = self._lower_types().get(guard.left.name)
            if type_def is not None:
                bound = guard.right.value
                if guard.op == CompOp.EQ and not type_def.min_value <= bound <= type_def.max_value:
                    return False
                if guard.op == CompOp.LT and type_def.min_value >= bound:
                    return False
                if guard.op == CompOp.LE and type_def.min_value > bound:
                    return False
                if guard.op == CompOp.GT and type_def.max_value <= bound:
                    return False
                if guard.op == CompOp.GE and type_def.max_value < bound:
                    return False
            value = equalities.get(guard.left.name)
            if value is None:
                continue
            bound = guard.right.value
            if guard.op == CompOp.LT and not value < bound:
                return False
            if guard.op == CompOp.LE and not value <= bound:
                return False
            if guard.op == CompOp.GT and not value > bound:
                return False
            if guard.op == CompOp.GE and not value >= bound:
                return False

        return True


def smv_to_gc(model: SmvModel) -> ParseResult:
    """Lower a parsed SMV model into the guarded-command ParseResult IR."""
    return _SmvLowerer(model).lower()


def smv_to_symbol_parse_result(model: SmvModel) -> ParseResult:
    """Build a lightweight ParseResult for symbol/reference checks.

    This does not lower next-state semantics. It exists so parser/support
    operations can use the common ParseResult interface without forcing command
    expansion.
    """
    lowerer = _SmvLowerer(model)
    return ParseResult(
        constants={},
        types=lowerer._lower_types(),
        init_condition=None,
        commands=[],
        ranking_functions={},
        automaton_transitions=[],
        automaton_initial_states=None,
        aps={},
        ltl_formula=None,
        observable_symbols=set(lowerer.variables) | set(lowerer._lower_observable_definitions()),
        observable_definitions=lowerer._lower_observable_definitions(),
    )


def import_smv(text: str) -> ParseResult:
    """Parse SMV text and lower it into the guarded-command ParseResult IR."""
    return smv_to_gc(parse_smv(text))


def import_smv_file(path: str) -> ParseResult:
    return import_smv(Path(path).read_text())
