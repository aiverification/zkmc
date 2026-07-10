"""AST types for a small, explicit subset of NuSMV/SMV."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SmvBooleanType:
    def __repr__(self) -> str:
        return "boolean"


@dataclass(frozen=True)
class SmvRangeType:
    min_value: int
    max_value: int

    def __post_init__(self) -> None:
        if self.min_value > self.max_value:
            raise ValueError(f"Invalid SMV range type: {self.min_value}..{self.max_value}")

    def __repr__(self) -> str:
        return f"{self.min_value}..{self.max_value}"


@dataclass(frozen=True)
class SmvEnumType:
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("SMV enum type must contain at least one value")

    def __repr__(self) -> str:
        return "{" + ", ".join(self.values) + "}"


SmvType = SmvBooleanType | SmvRangeType | SmvEnumType


@dataclass(frozen=True)
class SmvVar:
    name: str
    type: SmvType

    def __repr__(self) -> str:
        return f"{self.name} : {self.type}"


@dataclass(frozen=True)
class SmvInt:
    value: int

    def __repr__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class SmvBool:
    value: bool

    def __repr__(self) -> str:
        return "TRUE" if self.value else "FALSE"


@dataclass(frozen=True)
class SmvName:
    name: str

    def __repr__(self) -> str:
        return self.name


@dataclass(frozen=True)
class SmvSet:
    values: tuple["SmvExpr", ...]

    def __repr__(self) -> str:
        return "{" + ", ".join(str(v) for v in self.values) + "}"


@dataclass(frozen=True)
class SmvUnary:
    op: str
    expr: "SmvExpr"

    def __repr__(self) -> str:
        return f"({self.op}{self.expr})"


@dataclass(frozen=True)
class SmvBinary:
    op: str
    left: "SmvExpr"
    right: "SmvExpr"

    def __repr__(self) -> str:
        return f"({self.left} {self.op} {self.right})"


@dataclass(frozen=True)
class SmvCaseArm:
    guard: "SmvExpr"
    value: "SmvExpr"

    def __repr__(self) -> str:
        return f"{self.guard} : {self.value}"


@dataclass(frozen=True)
class SmvCase:
    arms: tuple[SmvCaseArm, ...]

    def __post_init__(self) -> None:
        if not self.arms:
            raise ValueError("SMV case expression must contain at least one arm")

    def __repr__(self) -> str:
        arms = "; ".join(str(arm) for arm in self.arms)
        return f"case {arms}; esac"


SmvExpr = SmvInt | SmvBool | SmvName | SmvSet | SmvUnary | SmvBinary | SmvCase


@dataclass(frozen=True)
class SmvDefine:
    name: str
    expr: SmvExpr

    def __repr__(self) -> str:
        return f"{self.name} := {self.expr}"


@dataclass(frozen=True)
class SmvAssignment:
    target: str
    expr: SmvExpr
    kind: Literal["init", "next", "current"]

    def __repr__(self) -> str:
        if self.kind == "current":
            return f"{self.target} := {self.expr}"
        return f"{self.kind}({self.target}) := {self.expr}"


@dataclass(frozen=True)
class SmvModel:
    module: str
    variables: tuple[SmvVar, ...]
    defines: tuple[SmvDefine, ...]
    assignments: tuple[SmvAssignment, ...]

    def variable_map(self) -> dict[str, SmvType]:
        return {var.name: var.type for var in self.variables}

    def define_map(self) -> dict[str, SmvExpr]:
        return {define.name: define.expr for define in self.defines}

    def init_assignments(self) -> tuple[SmvAssignment, ...]:
        return tuple(a for a in self.assignments if a.kind == "init")

    def next_assignments(self) -> tuple[SmvAssignment, ...]:
        return tuple(a for a in self.assignments if a.kind == "next")
