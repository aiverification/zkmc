"""AST types for ranking functions."""

from dataclasses import dataclass
from typing import Optional
from .ast_types import Comparison, Expr


@dataclass(frozen=True)
class RankingCase:
    """One case of a ranking function: guard -> expression or guard -> inf.

    Represents a single case in a piecewise ranking function:
        [] guard -> expression  (finite case: is_infinity=False)
        [] guard -> inf         (infinity case: is_infinity=True)

    The guard is a conjunction of comparisons (like in guarded commands).
    For finite cases: expression is a linear expression that computes the ranking value.
    For infinity cases: expression is None, and the ranking value is +∞.
    """
    guards: list[Comparison]      # conjunction of comparisons
    expression: Optional[Expr]    # linear expression (finite) or None (infinity)
    is_infinity: bool = False     # True for infinity cases, False for finite cases

    def __repr__(self) -> str:
        guard_str = " && ".join(str(g) for g in self.guards)
        if self.is_infinity:
            return f"[] {guard_str} -> inf"
        else:
            return f"[] {guard_str} -> {self.expression}"

    def get_variables(self) -> set[str]:
        """Extract all variable names from guards and expression."""
        from .ast_types import Var, BinOp, Neg
        variables: set[str] = set()

        def collect_from_expr(e: Expr) -> None:
            if isinstance(e, Var):
                variables.add(e.name)
            elif isinstance(e, BinOp):
                collect_from_expr(e.left)
                collect_from_expr(e.right)
            elif isinstance(e, Neg):
                collect_from_expr(e.expr)

        # Collect from guards
        for guard in self.guards:
            collect_from_expr(guard.left)
            collect_from_expr(guard.right)

        # Collect from expression (only for finite cases)
        if self.expression is not None:
            collect_from_expr(self.expression)

        return variables


@dataclass(frozen=True)
class RankingFunction:
    """Ranking function for one automaton state.

    Represents V(x, q) for a specific state q as a piecewise linear function:
        rank(q):
            [] guard_1 -> expression_1  (finite case)
            [] guard_2 -> expression_2  (finite case)
            [] guard_3 -> inf           (infinity case)
            ...

    Cases are checked in order (first-match semantics).
    Finite cases compute a ranking value; infinity cases assign V(x, q) = +∞.
    Cases must be disjoint and cover the entire state space (validated separately).
    """
    state: str                    # state name (e.g., "q0")
    cases: list[RankingCase]      # ordered list of cases

    def __repr__(self) -> str:
        cases_str = "\n  ".join(str(case) for case in self.cases)
        return f"rank({self.state}):\n  {cases_str}"

    def get_variables(self) -> set[str]:
        """Extract all variable names from all cases."""
        variables: set[str] = set()
        for case in self.cases:
            variables.update(case.get_variables())
        return variables
