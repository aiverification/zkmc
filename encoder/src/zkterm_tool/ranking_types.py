"""AST types for ranking functions."""

from dataclasses import dataclass
from .ast_types import Comparison, Expr


@dataclass(frozen=True)
class RankingCase:
    """One case of a ranking function: guard -> expression.

    Represents a single case in a piecewise ranking function:
        [] guard -> expression

    The guard is a conjunction of comparisons (like in guarded commands).
    The expression is a linear expression that computes the ranking value.
    """
    guards: list[Comparison]  # conjunction of comparisons
    expression: Expr          # linear expression (not assignment)

    def __repr__(self) -> str:
        guard_str = " && ".join(str(g) for g in self.guards)
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

        # Collect from expression
        collect_from_expr(self.expression)

        return variables


@dataclass(frozen=True)
class RankingFunction:
    """Ranking function for one automaton state.

    Represents V(x, q) for a specific state q as a piecewise linear function:
        rank(q):
            [] guard_1 -> expression_1
            [] guard_2 -> expression_2
            ...

    Cases are checked in order (first-match semantics).
    If no guard is satisfied, V(x, q) = +∞.
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
