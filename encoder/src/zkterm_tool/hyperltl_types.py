"""AST types for parser-only HyperLTL import."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


QuantifierKind = Literal["forall", "exists"]


@dataclass(frozen=True)
class TraceQuantifier:
    kind: QuantifierKind
    trace: str

    def __repr__(self) -> str:
        return f"{self.kind} {self.trace}."


@dataclass(frozen=True)
class HyperAtomRef:
    variable: str
    trace: str

    def __repr__(self) -> str:
        return f'"{self.variable}"_{self.trace}'


@dataclass(frozen=True)
class HyperAtom:
    raw: str
    references: tuple[HyperAtomRef, ...]

    def __repr__(self) -> str:
        return "{" + self.raw + "}"


@dataclass(frozen=True)
class HyperFormula:
    quantifiers: tuple[TraceQuantifier, ...]
    body: str
    atoms: tuple[HyperAtom, ...]

    def traces(self) -> tuple[str, ...]:
        return tuple(q.trace for q in self.quantifiers)

    def quantifier_kinds(self) -> tuple[QuantifierKind, ...]:
        return tuple(q.kind for q in self.quantifiers)
