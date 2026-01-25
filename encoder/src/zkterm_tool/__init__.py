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
    encode_infinity_case,
    RankingCaseEncoding, InfinityCaseEncoding, RankingFunctionEncoding
)
from .automaton_encoder import (
    encode_automaton_transition, encode_automaton_transitions,
    AutomatonTransitionEncoding
)
from .ranking_validator import (
    check_disjoint_cases, check_complete_coverage, check_non_negativity,
    validate_ranking_function
)
from .verification_types import VerificationResult, ObligationResult
from .verifier import Verifier
from .farkas_cli import extract_farkas_obligations
from .state_enumerator import StateSpace, StateBounds, create_state_space
from .ranking_evaluator import evaluate_ranking, check_guard
from .violation_checker import (
    ViolationSets,
    FieldEmbeddings,
    VerificationChecks,
    compute_violation_sets,
    compute_embeddings,
    verify_disjointness
)
from .explicit_cli import violations_to_json


def verify_termination(result: ParseResult) -> VerificationResult:
    """Verify termination obligations for a parsed program.

    Args:
        result: ParseResult from parse_with_constants()

    Returns:
        VerificationResult with all obligations checked

    Raises:
        ValueError: If required components (e.g., ranking functions) are missing

    Example:
        >>> result = parse_with_constants(program_text)
        >>> verification = verify_termination(result)
        >>> print(verification.summary())
        5/5 obligations verified
    """
    verifier = Verifier(result)
    return verifier.verify_all()


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
    "encode_infinity_case",
    "RankingCaseEncoding", "InfinityCaseEncoding", "RankingFunctionEncoding",
    # Automaton encoder
    "encode_automaton_transition", "encode_automaton_transitions",
    "AutomatonTransitionEncoding",
    # Ranking validation
    "check_disjoint_cases", "check_complete_coverage", "check_non_negativity",
    "validate_ranking_function",
    # Verification
    "VerificationResult", "ObligationResult", "Verifier", "verify_termination",
    "extract_farkas_obligations",
    # Explicit-state verification
    "StateSpace", "StateBounds", "create_state_space",
    "evaluate_ranking", "check_guard",
    "ViolationSets", "FieldEmbeddings", "VerificationChecks",
    "compute_violation_sets", "compute_embeddings", "verify_disjointness",
    "violations_to_json",
]
