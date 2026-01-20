"""zkterm-tool: Transform guarded commands into matrix/vector inequality form."""

from .ast_types import (
    GuardedCommand, Comparison, Assignment, CompOp,
    Var, Num, BinOp, Neg, Expr
)
from .parser import parse, parse_with_constants, ParseResult
from .encoder import encode_transition, encode_program, TransitionEncoding

__all__ = [
    # AST types
    "GuardedCommand", "Comparison", "Assignment", "CompOp",
    "Var", "Num", "BinOp", "Neg", "Expr",
    # Parser
    "parse", "parse_with_constants", "ParseResult",
    # Encoder
    "encode_transition", "encode_program", "TransitionEncoding",
]
