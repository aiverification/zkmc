"""AST types for guarded commands."""

from dataclasses import dataclass
from enum import Enum
from typing import Union


class CompOp(Enum):
    """Comparison operators."""
    LT = "<"      # strict less than
    LE = "<="     # less than or equal
    EQ = "="      # equality
    GE = ">="     # greater than or equal
    GT = ">"      # strict greater than


@dataclass(frozen=True)
class Var:
    """A variable reference."""
    name: str
    
    def __repr__(self) -> str:
        return self.name


@dataclass(frozen=True)
class Num:
    """A numeric constant."""
    value: int
    
    def __repr__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class BinOp:
    """A binary operation (add, sub, mul)."""
    op: str  # '+', '-', '*'
    left: "Expr"
    right: "Expr"
    
    def __repr__(self) -> str:
        return f"({self.left} {self.op} {self.right})"


@dataclass(frozen=True)
class Neg:
    """Unary negation."""
    expr: "Expr"
    
    def __repr__(self) -> str:
        return f"(-{self.expr})"


# Expression type
Expr = Union[Var, Num, BinOp, Neg]


@dataclass(frozen=True)
class Comparison:
    """A comparison between two expressions: left op right."""
    left: Expr
    right: Expr
    op: CompOp
    
    def __repr__(self) -> str:
        return f"{self.left} {self.op.value} {self.right}"


@dataclass(frozen=True)
class Assignment:
    """An assignment: var = expr."""
    var: str
    expr: Expr
    
    def __repr__(self) -> str:
        return f"{self.var} = {self.expr}"


@dataclass(frozen=True)
class GuardedCommand:
    """A guarded command: [] guard -> assignments."""
    guards: list[Comparison]  # conjunction of comparisons
    assignments: list[Assignment]
    
    def __repr__(self) -> str:
        guard_str = " && ".join(str(g) for g in self.guards)
        assign_str = "; ".join(str(a) for a in self.assignments)
        return f"[] {guard_str} -> {assign_str}"
    
    def get_variables(self) -> set[str]:
        """Extract all variable names from guards and assignments."""
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
        
        for assign in self.assignments:
            variables.add(assign.var)
            collect_from_expr(assign.expr)
        
        return variables
