"""Parser for guarded commands using Lark."""

from pathlib import Path
from lark import Lark, Transformer, v_args

from .ast_types import (
    GuardedCommand, Comparison, Assignment, CompOp,
    Var, Num, BinOp, Neg, Expr
)


# Load grammar from file
GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"


class ASTTransformer(Transformer):
    """Transform Lark parse tree into our AST types."""
    
    def start(self, items: list) -> list[GuardedCommand]:
        return list(items)
    
    def guarded_command(self, items: list) -> GuardedCommand:
        guard_comparisons = items[0]  # list of comparisons from guard
        assignments = items[1]  # list of assignments
        return GuardedCommand(guards=guard_comparisons, assignments=assignments)
    
    def guard(self, items: list) -> list[Comparison]:
        return list(items)
    
    def comparison(self, items: list) -> Comparison:
        left, op_token, right = items
        op_str = str(op_token)
        # Normalize operators
        if op_str in ("≤", "<="):
            op = CompOp.LE
        elif op_str == "<":
            op = CompOp.LT
        elif op_str in ("=", "=="):
            op = CompOp.EQ
        elif op_str in ("≥", ">="):
            op = CompOp.GE
        elif op_str == ">":
            op = CompOp.GT
        else:
            raise ValueError(f"Unknown comparison operator: {op_str}")
        return Comparison(left=left, right=right, op=op)
    
    def assignments(self, items: list) -> list[Assignment]:
        return list(items)
    
    def assignment(self, items: list) -> Assignment:
        var_token, expr = items
        return Assignment(var=str(var_token), expr=expr)
    
    @v_args(inline=True)
    def add(self, left: Expr, right: Expr) -> BinOp:
        return BinOp(op="+", left=left, right=right)
    
    @v_args(inline=True)
    def sub(self, left: Expr, right: Expr) -> BinOp:
        return BinOp(op="-", left=left, right=right)
    
    @v_args(inline=True)
    def mul(self, left: Expr, right: Expr) -> BinOp:
        return BinOp(op="*", left=left, right=right)
    
    @v_args(inline=True)
    def neg(self, expr: Expr) -> Neg:
        return Neg(expr=expr)
    
    @v_args(inline=True)
    def number(self, token) -> Num:
        return Num(value=int(token))
    
    @v_args(inline=True)
    def var(self, token) -> Var:
        return Var(name=str(token))


def create_parser() -> Lark:
    """Create a Lark parser for guarded commands."""
    grammar = GRAMMAR_PATH.read_text()
    return Lark(grammar, parser="lalr", transformer=ASTTransformer())


def parse(text: str) -> list[GuardedCommand]:
    """Parse guarded commands text into AST."""
    parser = create_parser()
    return parser.parse(text)
