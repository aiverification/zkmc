"""zkterm-tool: Transform guarded commands and ranking functions into matrix/vector forms."""

from .ast_types import (
    GuardedCommand, Comparison, Assignment, CompOp,
    Var, Num, BinOp, Neg, Expr
)
from .ranking_types import RankingCase, RankingFunction
from .parser import parse, parse_with_constants, ParseResult
from .encoder import encode_transition, encode_program, TransitionEncoding
from .ranking_encoder import (
    encode_ranking_case, encode_ranking_function, encode_ranking_functions,
    RankingCaseEncoding, RankingFunctionEncoding
)

__all__ = [
    # AST types
    "GuardedCommand", "Comparison", "Assignment", "CompOp",
    "Var", "Num", "BinOp", "Neg", "Expr",
    # Ranking types
    "RankingCase", "RankingFunction",
    # Parser
    "parse", "parse_with_constants", "ParseResult",
    # Encoder
    "encode_transition", "encode_program", "TransitionEncoding",
    # Ranking encoder
    "encode_ranking_case", "encode_ranking_function", "encode_ranking_functions",
    "RankingCaseEncoding", "RankingFunctionEncoding",
]
