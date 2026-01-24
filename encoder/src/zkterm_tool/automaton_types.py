"""AST types for Büchi automaton transitions."""

from dataclasses import dataclass
from .ast_types import Comparison


@dataclass(frozen=True)
class AutomatonTransition:
    """One transition in a Büchi automaton.

    Represents a transition: trans(q, q'): guard or trans!(q, q'): guard

    The guard is a conjunction of comparisons (like in guarded commands).
    Fair transitions (marked with !) are part of both δ and F.
    """
    from_state: str               # Source state (e.g., "q0")
    to_state: str                 # Target state (e.g., "q1")
    guards: list[Comparison]      # Conjunction of comparisons
    is_fair: bool                 # True if marked with ! (fair transition)

    def __repr__(self) -> str:
        fair_mark = "!" if self.is_fair else ""
        guard_str = " && ".join(str(g) for g in self.guards)
        return f"trans{fair_mark}({self.from_state}, {self.to_state}): {guard_str}"

    def get_variables(self) -> set[str]:
        """Extract all variable names from guards."""
        from .ast_types import Var, BinOp, Neg, Expr
        variables: set[str] = set()

        def collect_from_expr(e: Expr) -> None:
            if isinstance(e, Var):
                variables.add(e.name)
            elif isinstance(e, BinOp):
                collect_from_expr(e.left)
                collect_from_expr(e.right)
            elif isinstance(e, Neg):
                collect_from_expr(e.expr)

        for guard in self.guards:
            collect_from_expr(guard.left)
            collect_from_expr(guard.right)

        return variables
