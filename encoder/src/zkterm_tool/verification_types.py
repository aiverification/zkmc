"""Data types for verification results."""

from dataclasses import dataclass


@dataclass
class ObligationResult:
    """Result of checking one verification obligation.

    Attributes:
        obligation_type: Type of obligation ("initial" or "update")
        program_transition_idx: Index of program transition (for update obligations)
        automaton_transition: Tuple of (from_state, to_state) for automaton transition
        source_ranking_state: Source state for ranking function
        target_ranking_state: Target state for ranking function (update only)
        source_case_idx: Index of source ranking case (update only)
        is_fair: Whether this is a fair transition (update only)
        passed: Whether the obligation was verified
        witness: Farkas multipliers that prove the obligation (if passed)
    """
    obligation_type: str
    program_transition_idx: int | None
    automaton_transition: tuple[str, str] | None
    source_ranking_state: str | None
    target_ranking_state: str | None = None
    source_case_idx: int | None = None
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
            case_info = f"[case {self.source_case_idx}]" if self.source_case_idx is not None else ""
            parts.append(f"source={self.source_ranking_state}{case_info}")

        if self.target_ranking_state:
            parts.append(f"target={self.target_ranking_state}")

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
