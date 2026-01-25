"""Data types for verification results."""

from dataclasses import dataclass
from typing import Literal


@dataclass
class ObligationResult:
    """Result of checking one verification obligation.

    Three types of obligations:
    1. initial_non_infinity: Check that initial states don't satisfy infinity case guards
       - A_0 x ≤ b_0 => E_k x > f_k
    2. transition_non_infinity: Check that transitions from finite cases don't reach infinity
       - A_i [x;x'] ≤ b_i => [P; C_j] x ≤ [r; d_j] => E_k x > f_k
    3. update: Check ranking decrease and non-negativity
       - A_i [x;x'] ≤ b_i => [P 0; C_j 0; 0 C_k] [x;x'] ≤ [r; d_j; d_k] => [w_j, -w_k] [x;x'] > u_k - u_j + ζ

    Attributes:
        obligation_type: Type of obligation
        program_transition_idx: Index of program transition (for type 2 and 3)
        automaton_transition: Tuple of (from_state, to_state) for automaton transition
        source_ranking_state: Source state for ranking function
        target_ranking_state: Target state for ranking function (type 3 only)
        source_case_idx: Index of source finite case (type 2 and 3)
        target_case_idx: Index of target finite case (type 3 only)
        infinity_case_idx: Index of infinity case being checked (type 1 and 2)
        is_fair: Whether this is a fair transition (type 3 only)
        passed: Whether the obligation was verified
        witness: Farkas multipliers that prove the obligation (if passed)
    """
    obligation_type: Literal["initial_non_infinity", "transition_non_infinity", "update"]
    program_transition_idx: int | None
    automaton_transition: tuple[str, str] | None
    source_ranking_state: str | None
    target_ranking_state: str | None = None
    source_case_idx: int | None = None
    target_case_idx: int | None = None
    infinity_case_idx: int | None = None
    is_fair: bool = False
    passed: bool = False
    witness: dict[str, int] | None = None

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        parts = [f"{self.obligation_type}: {status}"]

        if self.program_transition_idx is not None:
            parts.append(f"prog_trans={self.program_transition_idx}")

        if self.automaton_transition:
            from_state, to_state = self.automaton_transition
            fair_mark = "!" if self.is_fair else ""
            parts.append(f"aut_trans{fair_mark}=({from_state},{to_state})")

        if self.source_ranking_state:
            if self.obligation_type in ["transition_non_infinity", "update"]:
                case_info = f"[finite_case {self.source_case_idx}]" if self.source_case_idx is not None else ""
            else:
                case_info = ""
            parts.append(f"source={self.source_ranking_state}{case_info}")

        if self.target_ranking_state:
            case_info = f"[finite_case {self.target_case_idx}]" if self.target_case_idx is not None else ""
            parts.append(f"target={self.target_ranking_state}{case_info}")

        if self.infinity_case_idx is not None:
            parts.append(f"infinity_case={self.infinity_case_idx}")

        return " ".join(parts)


@dataclass
class VerificationResult:
    """Complete verification result for a program.

    Attributes:
        passed: True if all obligations verified
        obligations: List of individual obligation results
    """
    passed: bool
    obligations: list[ObligationResult]

    def summary(self) -> str:
        """Human-readable summary of verification results.

        Returns:
            String like "5/5 obligations verified" or "3/5 obligations verified"
        """
        total = len(self.obligations)
        passed = sum(1 for o in self.obligations if o.passed)
        return f"{passed}/{total} obligations verified"

    def get_witnesses(self) -> list[dict[str, int]]:
        """Get all Farkas witnesses for passed obligations.

        Returns:
            List of witness dictionaries mapping variable names to integer values
        """
        return [o.witness for o in self.obligations if o.passed and o.witness is not None]

    def failed_obligations(self) -> list[ObligationResult]:
        """Get all failed obligations.

        Returns:
            List of obligations that failed verification
        """
        return [o for o in self.obligations if not o.passed]

    def __str__(self) -> str:
        lines = [self.summary()]
        if not self.passed:
            lines.append("\nFailed obligations:")
            for obl in self.failed_obligations():
                lines.append(f"  - {obl}")
        return "\n".join(lines)
