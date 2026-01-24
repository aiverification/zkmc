"""Data types for verification results."""

from dataclasses import dataclass


@dataclass
class ObligationResult:
    """Result of checking one verification obligation.

    Attributes:
        obligation_type: Type of obligation ("initial", "well_defined",
                        "non_increasing", "strictly_decreasing")
        program_transition_idx: Index of program transition (for transition obligations)
        automaton_transition: Tuple of (from_state, to_state) for automaton transition
        ranking_state: State name for ranking function
        passed: Whether the obligation was verified
        witness: Farkas multipliers that prove the obligation (if passed)
    """
    obligation_type: str
    program_transition_idx: int | None
    automaton_transition: tuple[str, str] | None
    ranking_state: str | None
    passed: bool
    witness: dict[str, int] | None = None

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        parts = [f"{self.obligation_type}: {status}"]

        if self.program_transition_idx is not None:
            parts.append(f"prog_trans={self.program_transition_idx}")
        if self.automaton_transition:
            from_state, to_state = self.automaton_transition
            parts.append(f"aut_trans=({from_state},{to_state})")
        if self.ranking_state:
            parts.append(f"state={self.ranking_state}")

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
