"""Main verification logic for termination obligations."""

from itertools import product
from typing import Dict, List
import numpy as np

from .parser import ParseResult
from .encoder import encode_program, encode_init, TransitionEncoding
from .ranking_encoder import encode_ranking_functions, RankingFunctionEncoding
from .automaton_encoder import encode_automaton_transitions, AutomatonTransitionEncoding
from .verification_types import ObligationResult, VerificationResult
from .farkas import build_farkas_dual
from .z3_solver import solve_farkas_dual
from typing import Tuple
from numpy.typing import NDArray


class Verifier:
    """Orchestrates verification of all termination obligations."""

    def __init__(self, result: ParseResult):
        """Initialize verifier with parsed program.

        Args:
            result: ParseResult from parse_with_constants()

        Raises:
            ValueError: If required components are missing
        """
        # Encode components (except init, which needs the variable list first)
        self.trans_encs = encode_program(result.commands, nonstrict_only=True) if result.commands else []
        self.rank_encs = encode_ranking_functions(result.ranking_functions) if result.ranking_functions else {}
        self.aut_encs = encode_automaton_transitions(result.automaton_transitions) if result.automaton_transitions else []

        # Verify we have all required components
        if not self.rank_encs:
            raise ValueError("No ranking functions provided - cannot verify termination")

        # Extract variable ordering from program transitions (they have all variables)
        # If no transitions, fall back to ranking functions
        if self.trans_encs:
            # Program transitions use [x, x'] space, extract first n variables
            self.variables = self.trans_encs[0].variables
        else:
            first_rank = next(iter(self.rank_encs.values()))
            self.variables = first_rank.variables

        # Now encode init condition with the correct variable list
        # Note: Use 'is not None' because empty list [] is falsy but valid (means 'true')
        self.init_enc = encode_init(result.init_condition, self.variables) if result.init_condition is not None else None

    def _align_and_expand(
        self,
        A: NDArray[np.int64],
        b: NDArray[np.int64],
        constraint_vars: List[str],
        primed: bool
    ) -> Tuple[NDArray[np.int64], NDArray[np.int64]]:
        """Align constraint from subset of vars to full vars, then expand to [x, x'].

        Args:
            A: Constraint matrix in constraint_vars space
            b: Constraint vector
            constraint_vars: Variables that A refers to
            primed: If True, apply to x' variables; if False, apply to x variables

        Returns:
            (A_expanded, b_expanded) in [x, x'] space
        """
        m = A.shape[0]
        n = len(self.variables)

        # Handle empty constraints (0 rows) - still need proper column count
        if m == 0:
            # Return empty matrix with correct dimensions for [x, x'] space
            A_exp = np.zeros((0, 2*n), dtype=np.int64)
            return A_exp, b

        # First, align to full variable space if needed
        if constraint_vars != self.variables:
            # Create mapping from constraint vars to full vars
            A_aligned = np.zeros((m, n), dtype=np.int64)
            for i, var in enumerate(constraint_vars):
                if var in self.variables:
                    j = self.variables.index(var)
                    A_aligned[:, j] = A[:, i]
            A = A_aligned

        # Then expand to [x, x'] space
        if primed:
            A_exp = np.hstack([np.zeros((m, n), dtype=np.int64), A])
        else:
            A_exp = np.hstack([A, np.zeros((m, n), dtype=np.int64)])

        return A_exp, b

    @staticmethod
    def _expand_to_transition_space(
        A: NDArray[np.int64],
        b: NDArray[np.int64],
        primed: bool,
        n: int
    ) -> Tuple[NDArray[np.int64], NDArray[np.int64]]:
        """Expand constraint from [x] space to [x, x'] space.

        Args:
            A: Constraint matrix in [x] space (already aligned to full variable set)
            b: Constraint vector in [x] space
            primed: If True, apply to x' variables; if False, apply to x variables
            n: Number of variables (dimension of x)

        Returns:
            (A_expanded, b_expanded) in [x, x'] space
        """
        if A.size == 0:
            # Empty constraint - return empty in expanded space
            return A, b

        m = A.shape[0]  # Number of constraints

        if primed:
            # Apply to x': [0 | A] [x; x'] ≤ b
            A_exp = np.hstack([np.zeros((m, n), dtype=np.int64), A])
        else:
            # Apply to x: [A | 0] [x; x'] ≤ b
            A_exp = np.hstack([A, np.zeros((m, n), dtype=np.int64)])

        return A_exp, b

    def verify_all(self) -> VerificationResult:
        """Run all verification obligations.

        Returns:
            VerificationResult with all obligations checked
        """
        obligations = []

        # 1. Initial condition obligations
        obligations.extend(self._verify_initial())

        # 2. Transition obligations
        # For each (program transition, automaton transition) pair
        for prog_idx, prog_trans in enumerate(self.trans_encs):
            for aut_trans in self.aut_encs:
                # Well-definedness
                obligations.extend(self._verify_well_defined(prog_idx, prog_trans, aut_trans))

                # Non-increasing
                obligations.extend(self._verify_non_increasing(prog_idx, prog_trans, aut_trans))

                # Strictly decreasing (only for fair transitions)
                if aut_trans.is_fair:
                    obligations.extend(self._verify_strictly_decreasing(prog_idx, prog_trans, aut_trans))

        return VerificationResult(
            passed=all(o.passed for o in obligations),
            obligations=obligations
        )

    def _verify_initial(self) -> List[ObligationResult]:
        """Verify initial condition obligations.

        For each automaton state q with ranking function:
            A_0 x ≤ b_0 ∧ A_V^(q) x ≤ b_V^(q) ⟹ C_V^(q) x + d_V^(q) > 0

        This checks that:
        1. Initial states satisfy the ranking function guard (well-defined)
        2. Ranking function value is positive at initial states

        Returns:
            List of ObligationResult for each state with ranking function
        """
        obligations = []

        # If no initial condition, skip (or should we require it?)
        if not self.init_enc:
            return obligations

        # For each state with a ranking function
        for state, rank_enc in self.rank_encs.items():
            # For single-case ranking functions, use the first (and only) case
            if len(rank_enc.cases) == 0:
                # No cases - ranking function is always undefined (infinity)
                # This is likely an error, but we'll mark it as failed
                obligations.append(ObligationResult(
                    obligation_type="initial",
                    program_transition_idx=None,
                    automaton_transition=None,
                    ranking_state=state,
                    passed=False,
                    witness=None
                ))
                continue

            # Use first case (for single-case ranking functions, this is the only case)
            case = rank_enc.cases[0]

            # Build Farkas dual for:
            # A_0 x ≤ b_0 ∧ A_V x ≤ b_V ⟹ C_V x + d_V >= 0
            # For strict inequality in Farkas: C_V x + d_V > -1
            #
            # Premise A_s: A_0 x ≤ b_0
            A_s = self.init_enc.A_0
            b_s = self.init_enc.b_0

            # Additional premise A_p: A_V x ≤ b_V (ranking guard)
            A_p = case.A_j
            b_p = case.b_j

            # Conclusion: C_V x + d_V > -1 (i.e., >= 0 for integers)
            # This is C_V x > -d_V - 1
            C_p = case.C_j.reshape(1, -1)  # Ensure it's a row vector
            d_p = np.array([-case.d_j - 1], dtype=np.int64)

            # Build and solve Farkas dual
            dual = build_farkas_dual(A_s, b_s, A_p, b_p, C_p, d_p)
            sat, witness = solve_farkas_dual(dual)

            obligations.append(ObligationResult(
                obligation_type="initial",
                program_transition_idx=None,
                automaton_transition=None,
                ranking_state=state,
                passed=sat,
                witness=witness
            ))

        return obligations

    def _verify_well_defined(
        self,
        prog_idx: int,
        prog_trans: TransitionEncoding,
        aut_trans: AutomatonTransitionEncoding
    ) -> List[ObligationResult]:
        """Verify ranking function well-definedness after transition.

        For transition from state q to q', checks:
            T(x,x') ∧ A_Σ x ≤ b_Σ ∧ A_V^(q) x ≤ b_V ⟹
                A_V^(q') x' ≤ b_V^(q') ∧ C_V^(q') x' + d_V^(q') > 0

        This ensures the ranking function for the target state is well-defined
        and positive.

        Args:
            prog_idx: Index of program transition
            prog_trans: Program transition encoding
            aut_trans: Automaton transition encoding

        Returns:
            List of ObligationResult (one per state transition)
        """
        obligations = []

        from_state = aut_trans.from_state
        to_state = aut_trans.to_state

        # Get ranking functions for source and target states
        if from_state not in self.rank_encs or to_state not in self.rank_encs:
            # Skip if ranking functions not defined for this transition
            return obligations

        rank_from = self.rank_encs[from_state]
        rank_to = self.rank_encs[to_state]

        # Use first case (single-case ranking functions)
        if len(rank_from.cases) == 0 or len(rank_to.cases) == 0:
            return obligations

        case_from = rank_from.cases[0]
        case_to = rank_to.cases[0]

        n = len(self.variables)

        # Build premise: T(x,x') ∧ A_Σ x ≤ b_Σ ∧ A_V^(q) x ≤ b_V
        # 1. Transition constraints (already in [x, x'] space)
        A_trans = prog_trans.A
        b_trans = prog_trans.b

        # 2. Automaton transition guard (align and expand from [x] to [x, x'])
        A_sigma_exp, b_sigma_exp = self._align_and_expand(
            aut_trans.A_delta, aut_trans.b_delta, aut_trans.variables, primed=False
        )

        # 3. Source ranking guard (align and expand from [x] to [x, x'])
        A_rank_from_exp, b_rank_from_exp = self._align_and_expand(
            case_from.A_j, case_from.b_j, rank_from.variables, primed=False
        )

        # Stack all premise constraints
        A_s = np.vstack([A_trans, A_sigma_exp, A_rank_from_exp])
        b_s = np.concatenate([b_trans, b_sigma_exp, b_rank_from_exp])

        # Build conclusion: A_V^(q') x' ≤ b_V^(q') ∧ C_V^(q') x' + d_V^(q') >= 0
        # We'll combine these by checking the ranking value is non-negative
        # when the guard is satisfied.
        #
        # Add target ranking guard to premises (align and expand)
        A_rank_to_exp, b_rank_to_exp = self._align_and_expand(
            case_to.A_j, case_to.b_j, rank_to.variables, primed=True
        )

        A_p = A_rank_to_exp
        b_p = b_rank_to_exp

        # Conclusion: C_V^(q') x' + d_V^(q') > -1 (i.e., >= 0 for integers)
        # This is: C_V^(q') x' > -d_V^(q') - 1
        # First align C_j to full variable space, then expand to [x, x'] (apply to x')
        C_j = case_to.C_j.reshape(1, -1)

        # Align C_j to full variable space if needed
        if rank_to.variables != self.variables:
            C_j_aligned = np.zeros((1, n), dtype=np.int64)
            for i, var in enumerate(rank_to.variables):
                if var in self.variables:
                    j = self.variables.index(var)
                    C_j_aligned[0, j] = C_j[0, i]
            C_j = C_j_aligned

        # Expand to [x, x'] space (apply to x')
        C_rank_to_exp = np.hstack([
            np.zeros((1, n), dtype=np.int64),
            C_j
        ])

        C_p = C_rank_to_exp
        d_p = np.array([-case_to.d_j - 1], dtype=np.int64)

        # Build and solve Farkas dual
        dual = build_farkas_dual(A_s, b_s, A_p, b_p, C_p, d_p)
        sat, witness = solve_farkas_dual(dual)

        obligations.append(ObligationResult(
            obligation_type="well_defined",
            program_transition_idx=prog_idx,
            automaton_transition=(from_state, to_state),
            ranking_state=to_state,
            passed=sat,
            witness=witness
        ))

        return obligations

    def _verify_non_increasing(
        self,
        prog_idx: int,
        prog_trans: TransitionEncoding,
        aut_trans: AutomatonTransitionEncoding
    ) -> List[ObligationResult]:
        """Verify ranking function does not increase.

        For transition from state q to q', checks:
            T(x,x') ∧ A_Σ x ≤ b_Σ ∧ A_V^(q) x ≤ b_V ⟹ V(x,q) ≥ V(x',q')

        Where V(x,q) = C_V^(q) x + d_V^(q).
        This becomes: C_V^(q) x + d_V^(q) ≥ C_V^(q') x' + d_V^(q')
        Rearranging: C_V^(q) x - C_V^(q') x' ≥ d_V^(q') - d_V^(q)
        Or: [C_V^(q), -C_V^(q')] [x; x'] ≥ d_V^(q') - d_V^(q)
        Strict form: [C_V^(q), -C_V^(q')] [x; x'] > d_V^(q') - d_V^(q) - 1

        Args:
            prog_idx: Index of program transition
            prog_trans: Program transition encoding
            aut_trans: Automaton transition encoding

        Returns:
            List of ObligationResult
        """
        obligations = []

        from_state = aut_trans.from_state
        to_state = aut_trans.to_state

        # Get ranking functions
        if from_state not in self.rank_encs or to_state not in self.rank_encs:
            return obligations

        rank_from = self.rank_encs[from_state]
        rank_to = self.rank_encs[to_state]

        if len(rank_from.cases) == 0 or len(rank_to.cases) == 0:
            return obligations

        case_from = rank_from.cases[0]
        case_to = rank_to.cases[0]

        n = len(self.variables)

        # Build premise: T(x,x') ∧ A_Σ x ≤ b_Σ ∧ A_V^(q) x ≤ b_V
        A_trans = prog_trans.A
        b_trans = prog_trans.b

        A_sigma_exp, b_sigma_exp = self._align_and_expand(
            aut_trans.A_delta, aut_trans.b_delta, aut_trans.variables, primed=False
        )

        A_rank_from_exp, b_rank_from_exp = self._align_and_expand(
            case_from.A_j, case_from.b_j, rank_from.variables, primed=False
        )

        A_s = np.vstack([A_trans, A_sigma_exp, A_rank_from_exp])
        b_s = np.concatenate([b_trans, b_sigma_exp, b_rank_from_exp])

        # Additional premise: target ranking guard
        A_rank_to_exp, b_rank_to_exp = self._align_and_expand(
            case_to.A_j, case_to.b_j, rank_to.variables, primed=True
        )

        A_p = A_rank_to_exp
        b_p = b_rank_to_exp

        # Conclusion: V(x,q) - V(x',q') > -1
        # [C_V^(q), -C_V^(q')] [x; x'] > d_V^(q') - d_V^(q) - 1

        # Align C_j coefficients to full variable space
        C_from = case_from.C_j.reshape(1, -1)
        C_to = case_to.C_j.reshape(1, -1)

        if rank_from.variables != self.variables:
            C_from_aligned = np.zeros((1, n), dtype=np.int64)
            for i, var in enumerate(rank_from.variables):
                if var in self.variables:
                    j = self.variables.index(var)
                    C_from_aligned[0, j] = C_from[0, i]
            C_from = C_from_aligned

        if rank_to.variables != self.variables:
            C_to_aligned = np.zeros((1, n), dtype=np.int64)
            for i, var in enumerate(rank_to.variables):
                if var in self.variables:
                    j = self.variables.index(var)
                    C_to_aligned[0, j] = C_to[0, i]
            C_to = C_to_aligned

        # Expand to [x, x'] space
        C_from_exp = np.hstack([C_from, np.zeros((1, n), dtype=np.int64)])
        C_to_exp = np.hstack([np.zeros((1, n), dtype=np.int64), C_to])

        C_p = C_from_exp - C_to_exp
        d_p = np.array([case_to.d_j - case_from.d_j - 1], dtype=np.int64)

        # Build and solve Farkas dual
        dual = build_farkas_dual(A_s, b_s, A_p, b_p, C_p, d_p)
        sat, witness = solve_farkas_dual(dual)

        obligations.append(ObligationResult(
            obligation_type="non_increasing",
            program_transition_idx=prog_idx,
            automaton_transition=(from_state, to_state),
            ranking_state=from_state,
            passed=sat,
            witness=witness
        ))

        return obligations

    def _verify_strictly_decreasing(
        self,
        prog_idx: int,
        prog_trans: TransitionEncoding,
        aut_trans: AutomatonTransitionEncoding
    ) -> List[ObligationResult]:
        """Verify ranking function strictly decreases on fair transitions.

        For fair transition from state q to q', checks:
            T(x,x') ∧ A_Σ x ≤ b_Σ ∧ A_V^(q) x ≤ b_V ⟹ V(x,q) > V(x',q')

        This becomes: [C_V^(q), -C_V^(q')] [x; x'] > d_V^(q') - d_V^(q)

        Args:
            prog_idx: Index of program transition
            prog_trans: Program transition encoding
            aut_trans: Automaton transition encoding

        Returns:
            List of ObligationResult
        """
        obligations = []

        from_state = aut_trans.from_state
        to_state = aut_trans.to_state

        # Get ranking functions
        if from_state not in self.rank_encs or to_state not in self.rank_encs:
            return obligations

        rank_from = self.rank_encs[from_state]
        rank_to = self.rank_encs[to_state]

        if len(rank_from.cases) == 0 or len(rank_to.cases) == 0:
            return obligations

        case_from = rank_from.cases[0]
        case_to = rank_to.cases[0]

        n = len(self.variables)

        # Build premise
        A_trans = prog_trans.A
        b_trans = prog_trans.b

        A_sigma_exp, b_sigma_exp = self._align_and_expand(
            aut_trans.A_delta, aut_trans.b_delta, aut_trans.variables, primed=False
        )

        A_rank_from_exp, b_rank_from_exp = self._align_and_expand(
            case_from.A_j, case_from.b_j, rank_from.variables, primed=False
        )

        A_s = np.vstack([A_trans, A_sigma_exp, A_rank_from_exp])
        b_s = np.concatenate([b_trans, b_sigma_exp, b_rank_from_exp])

        # Additional premise: target ranking guard
        A_rank_to_exp, b_rank_to_exp = self._align_and_expand(
            case_to.A_j, case_to.b_j, rank_to.variables, primed=True
        )

        A_p = A_rank_to_exp
        b_p = b_rank_to_exp

        # Conclusion: V(x,q) - V(x',q') > 0
        # [C_V^(q), -C_V^(q')] [x; x'] > d_V^(q') - d_V^(q)

        # Align C_j coefficients to full variable space
        C_from = case_from.C_j.reshape(1, -1)
        C_to = case_to.C_j.reshape(1, -1)

        if rank_from.variables != self.variables:
            C_from_aligned = np.zeros((1, n), dtype=np.int64)
            for i, var in enumerate(rank_from.variables):
                if var in self.variables:
                    j = self.variables.index(var)
                    C_from_aligned[0, j] = C_from[0, i]
            C_from = C_from_aligned

        if rank_to.variables != self.variables:
            C_to_aligned = np.zeros((1, n), dtype=np.int64)
            for i, var in enumerate(rank_to.variables):
                if var in self.variables:
                    j = self.variables.index(var)
                    C_to_aligned[0, j] = C_to[0, i]
            C_to = C_to_aligned

        # Expand to [x, x'] space
        C_from_exp = np.hstack([C_from, np.zeros((1, n), dtype=np.int64)])
        C_to_exp = np.hstack([np.zeros((1, n), dtype=np.int64), C_to])

        C_p = C_from_exp - C_to_exp
        d_p = np.array([case_to.d_j - case_from.d_j], dtype=np.int64)

        # Build and solve Farkas dual
        dual = build_farkas_dual(A_s, b_s, A_p, b_p, C_p, d_p)
        sat, witness = solve_farkas_dual(dual)

        obligations.append(ObligationResult(
            obligation_type="strictly_decreasing",
            program_transition_idx=prog_idx,
            automaton_transition=(from_state, to_state),
            ranking_state=from_state,
            passed=sat,
            witness=witness
        ))

        return obligations

    def _get_obligation_matrices(self, obl_result: ObligationResult) -> dict:
        """Reconstruct matrices for an obligation result.

        Returns dictionary with keys: A_s, b_s, A_p, b_p, C_p, d_p
        """
        n = len(self.variables)

        if obl_result.obligation_type == "initial":
            # Initial obligation
            state = obl_result.ranking_state
            rank_enc = self.rank_encs[state]
            case = rank_enc.cases[0]

            A_s = self.init_enc.A_0
            b_s = self.init_enc.b_0
            A_p = case.A_j
            b_p = case.b_j
            C_p = case.C_j.reshape(1, -1)
            d_p = np.array([-case.d_j - 1], dtype=np.int64)

        elif obl_result.obligation_type in ["well_defined", "non_increasing", "strictly_decreasing"]:
            # Transition obligations
            prog_idx = obl_result.program_transition_idx
            from_state, to_state = obl_result.automaton_transition

            prog_trans = self.trans_encs[prog_idx]
            aut_trans = next(a for a in self.aut_encs
                           if a.from_state == from_state and a.to_state == to_state)

            rank_from = self.rank_encs[from_state]
            rank_to = self.rank_encs[to_state]
            case_from = rank_from.cases[0]
            case_to = rank_to.cases[0]

            # Build premise (same for all transition obligations)
            A_trans = prog_trans.A
            b_trans = prog_trans.b

            A_sigma_exp, b_sigma_exp = self._align_and_expand(
                aut_trans.A_delta, aut_trans.b_delta, aut_trans.variables, primed=False
            )

            A_rank_from_exp, b_rank_from_exp = self._align_and_expand(
                case_from.A_j, case_from.b_j, rank_from.variables, primed=False
            )

            A_s = np.vstack([A_trans, A_sigma_exp, A_rank_from_exp])
            b_s = np.concatenate([b_trans, b_sigma_exp, b_rank_from_exp])

            # Additional premise and conclusion depend on obligation type
            if obl_result.obligation_type == "well_defined":
                A_rank_to_exp, b_rank_to_exp = self._align_and_expand(
                    case_to.A_j, case_to.b_j, rank_to.variables, primed=True
                )

                A_p = A_rank_to_exp
                b_p = b_rank_to_exp

                # Align C_j
                C_j_to = case_to.C_j.reshape(1, -1)
                if rank_to.variables != self.variables:
                    C_j_to_aligned = np.zeros((1, n), dtype=np.int64)
                    for i, var in enumerate(rank_to.variables):
                        if var in self.variables:
                            j = self.variables.index(var)
                            C_j_to_aligned[0, j] = C_j_to[0, i]
                    C_j_to = C_j_to_aligned

                C_rank_to_exp = np.hstack([np.zeros((1, n), dtype=np.int64), C_j_to])

                C_p = C_rank_to_exp
                d_p = np.array([-case_to.d_j - 1], dtype=np.int64)

            else:  # non_increasing or strictly_decreasing
                A_rank_to_exp, b_rank_to_exp = self._align_and_expand(
                    case_to.A_j, case_to.b_j, rank_to.variables, primed=True
                )

                A_p = A_rank_to_exp
                b_p = b_rank_to_exp

                # Align C_j coefficients
                C_j_from = case_from.C_j.reshape(1, -1)
                if rank_from.variables != self.variables:
                    C_j_from_aligned = np.zeros((1, n), dtype=np.int64)
                    for i, var in enumerate(rank_from.variables):
                        if var in self.variables:
                            j = self.variables.index(var)
                            C_j_from_aligned[0, j] = C_j_from[0, i]
                    C_j_from = C_j_from_aligned

                C_j_to = case_to.C_j.reshape(1, -1)
                if rank_to.variables != self.variables:
                    C_j_to_aligned = np.zeros((1, n), dtype=np.int64)
                    for i, var in enumerate(rank_to.variables):
                        if var in self.variables:
                            j = self.variables.index(var)
                            C_j_to_aligned[0, j] = C_j_to[0, i]
                    C_j_to = C_j_to_aligned

                C_from_exp = np.hstack([C_j_from, np.zeros((1, n), dtype=np.int64)])
                C_to_exp = np.hstack([np.zeros((1, n), dtype=np.int64), C_j_to])

                C_p = C_from_exp - C_to_exp

                if obl_result.obligation_type == "non_increasing":
                    d_p = np.array([case_to.d_j - case_from.d_j - 1], dtype=np.int64)
                else:  # strictly_decreasing
                    d_p = np.array([case_to.d_j - case_from.d_j], dtype=np.int64)

        else:
            raise ValueError(f"Unknown obligation type: {obl_result.obligation_type}")

        return {
            "A_s": A_s,
            "b_s": b_s,
            "A_p": A_p,
            "b_p": b_p,
            "C_p": C_p,
            "d_p": d_p
        }
