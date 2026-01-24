"""zkterm-tool: Transform guarded commands, ranking functions, init conditions, and automaton transitions."""

from .ast_types import (
    GuardedCommand, Comparison, Assignment, CompOp,
    Var, Num, BinOp, Neg, Expr
)
from .ranking_types import RankingCase, RankingFunction
from .automaton_types import AutomatonTransition
from .parser import parse, parse_with_constants, ParseResult
from .encoder import encode_transition, encode_program, encode_init, TransitionEncoding, InitEncoding
from .ranking_encoder import (
    encode_ranking_case, encode_ranking_function, encode_ranking_functions,
    RankingCaseEncoding, RankingFunctionEncoding
)
from .automaton_encoder import (
    encode_automaton_transition, encode_automaton_transitions,
    AutomatonTransitionEncoding
)

__all__ = [
    # AST types
    "GuardedCommand", "Comparison", "Assignment", "CompOp",
    "Var", "Num", "BinOp", "Neg", "Expr",
    # Ranking types
    "RankingCase", "RankingFunction",
    # Automaton types
    "AutomatonTransition",
    # Parser
    "parse", "parse_with_constants", "ParseResult",
    # Encoder
    "encode_transition", "encode_program", "encode_init",
    "TransitionEncoding", "InitEncoding",
    # Ranking encoder
    "encode_ranking_case", "encode_ranking_function", "encode_ranking_functions",
    "RankingCaseEncoding", "RankingFunctionEncoding",
    # Automaton encoder
    "encode_automaton_transition", "encode_automaton_transitions",
    "AutomatonTransitionEncoding",
]
