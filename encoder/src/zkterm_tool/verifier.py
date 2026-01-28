"""Main verification logic for termination obligations."""

from itertools import product
from typing import Dict, List
import warnings
import numpy as np

from .parser import ParseResult
from .encoder import encode_program, encode_init, TransitionEncoding
from .ranking_encoder import encode_ranking_functions, RankingFunctionEncoding
from .automaton_encoder import encode_automaton_transitions, AutomatonTransitionEncoding
from .verification_types import ObligationResult, VerificationResult
from .farkas import build_farkas_dual
from .z3_solver import solve_farkas_dual
from .ast_types import Var, BinOp, Neg, Expr
from typing import Tuple
from numpy.typing import NDArray


class Verifier:
    """Orchestrates verification of all termination obligations."""

    @staticmethod
    def _extract_vars_from_expr(expr: Expr) -> set[str]:
        """Extract all variable names from an expression.

        Args:
            expr: Expression to extract variables from

        Returns:
            Set of variable names found in the expression
        """
        vars_set: set[str] = set()
        if isinstance(expr, Var):
            vars_set.add(expr.name)
        elif isinstance(expr, BinOp):
            vars_set.update(Verifier._extract_vars_from_expr(expr.left))
            vars_set.update(Verifier._extract_vars_from_expr(expr.right))
        elif isinstance(expr, Neg):
            vars_set.update(Verifier._extract_vars_from_expr(expr.expr))
        # Num has no variables
        return vars_set

    def __init__(self, result: ParseResult):
        """Initialize verifier with parsed program.

        Args:
            result: ParseResult from parse_with_constants()

        Raises:
            ValueError: If required components are missing
        """
        # Verify we have all required components
        if not result.ranking_functions:
            raise ValueError("No ranking functions provided - cannot verify termination")

        if not result.automaton_transitions:
            raise ValueError(
                "No automaton transitions provided. "
                "Automaton transitions are required for termination verification. "
                "Add at least one 'trans(q, q'): guard' or 'trans!(q, q'): guard' declaration."
            )

        # First, determine the full variable set from all components
        # This ensures ranking functions are encoded with all program variables
        all_vars: set[str] = set()

        # Collect variables from program transitions
        if result.commands:
            for cmd in result.commands:
                all_vars.update(cmd.get_variables())

        # Collect variables from ranking functions
        for rf in result.ranking_functions.values():
            all_vars.update(rf.get_variables())

        # Collect variables from automaton transitions
        if result.automaton_transitions:
            for trans in result.automaton_transitions:
                all_vars.update(trans.get_variables())

        # Collect variables from init condition
        if result.init_condition:
            for guard in result.init_condition:
                # Collect from guard comparisons
                for expr in [guard.left, guard.right]:
                    all_vars.update(self._extract_vars_from_expr(expr))

        self.variables = sorted(all_vars)

        # Now encode all components with the correct variable list
        self.trans_encs = encode_program(result.commands, nonstrict_only=True, types=result.types) if result.commands else []
        self.aut_encs = encode_automaton_transitions(result.automaton_transitions) if result.automaton_transitions else []

        # Encode ranking functions with the full variable list
        # NOTE: Ranking functions do NOT get type bounds injected (need unbounded guards for completeness)
        from .ranking_encoder import encode_ranking_function
        self.rank_encs = {
            state: encode_ranking_function(rf, self.variables)
            for state, rf in result.ranking_functions.items()
        }

        # Encode init condition with the full variable list
        # Note: Use 'is not None' because empty list [] is falsy but valid (means 'true')
        self.init_enc = encode_init(result.init_condition, self.variables, types=result.types) if result.init_condition is not None else None

        # Store automaton initial states (Q_0)
        # If not specified, default to all states with ranking functions
        self.automaton_initial_states = result.automaton_initial_states if result.automaton_initial_states is not None else list(result.ranking_functions.keys())

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

        New verification system with three obligation types:
        - Type 1 (initial_non_infinity): Check initial states don't satisfy infinity guards
        - Type 2 (transition_non_infinity): Check transitions from finite cases don't reach infinity
        - Type 3 (update): Check ranking decrease and non-negativity

        Returns:
            VerificationResult with all obligations checked
        """
        obligations = []

        # Type 1: Initial non-infinity obligations (one per state × infinity case)
        # Only check states in Q_0 (automaton initial states)
        for state in self.automaton_initial_states:
            if state in self.rank_encs:
                obligations.extend(self._verify_initial_non_infinity(state))

        # Type 2: Transition non-infinity obligations
        # (one per prog_trans × aut_trans × finite_case(source) × infinity_case(target))
        for prog_idx, prog_trans in enumerate(self.trans_encs):
            for aut_trans in self.aut_encs:
                obligations.extend(
                    self._verify_transition_non_infinity(prog_idx, prog_trans, aut_trans)
                )

        # Type 3: Update obligations
        # (one per prog_trans × aut_trans × source_finite_case × target_finite_case)
        for prog_idx, prog_trans in enumerate(self.trans_encs):
            for aut_trans in self.aut_encs:
                obligations.extend(
                    self._verify_update(prog_idx, prog_trans, aut_trans)
                )

        return VerificationResult(
            passed=all(o.passed for o in obligations),
            obligations=obligations
        )

    def _verify_initial_non_infinity(self, state: str) -> List[ObligationResult]:
        """Type 1: Verify initial states don't satisfy infinity case guards.

        For each infinity case k:
            A_0 x ≤ b_0 => E_k x > f_k

        This checks that initial states don't have ranking value +∞.
        Should only be called for states in Q_0 (automaton_initial_states).

        Args:
            state: Automaton state in Q_0 with ranking function

        Returns:
            List of ObligationResult (one per infinity case)
        """
        obligations = []

        # If no initial condition, skip
        if not self.init_enc:
            return obligations

        rank_enc = self.rank_encs[state]

        # For each infinity case
        for inf_case_idx, inf_case in enumerate(rank_enc.infinity_cases):
            # Premise: A_0 x ≤ b_0
            A_s = self.init_enc.A_0
            b_s = self.init_enc.b_0

            # Conclusion: E_k x > f_k
            E = inf_case.E_k
            f = inf_case.f_k

            # Build Farkas dual (no middle premise)
            dual = build_farkas_dual(A_s, b_s, E, f)

            # Solve
            sat, witness = solve_farkas_dual(dual)

            obligations.append(ObligationResult(
                obligation_type="initial_non_infinity",
                program_transition_idx=None,
                automaton_transition=None,
                source_ranking_state=state,
                target_ranking_state=None,
                source_case_idx=None,
                target_case_idx=None,
                infinity_case_idx=inf_case_idx,
                is_fair=False,
                passed=sat,
                witness=witness if sat else None
            ))

        return obligations


    def _verify_transition_non_infinity(
        self,
        prog_idx: int,
        prog_trans: TransitionEncoding,
        aut_trans: AutomatonTransitionEncoding
    ) -> List[ObligationResult]:
        """Type 2: Verify transitions from finite cases don't reach infinity.

        For automaton transition (q, σ, q') ∈ δ:
        For each finite case j in rank(q) and infinity case k in rank(q'):
            A_i [x;x'] ≤ b_i => [P; C_j] x ≤ [r; d_j] => E_k x' > f_k

        This checks that from finite ranking values in source state q,
        we don't transition to +∞ in target state q'.

        Args:
            prog_idx: Index of program transition
            prog_trans: Program transition encoding
            aut_trans: Automaton transition encoding (q, σ, q')

        Returns:
            List of ObligationResult (one per finite_case(q) × infinity_case(q') pair)
        """
        obligations = []

        # Get source and target states from automaton transition
        source_state = aut_trans.from_state
        target_state = aut_trans.to_state

        # Skip if states don't have ranking functions
        if source_state not in self.rank_encs or target_state not in self.rank_encs:
            return obligations

        source_enc = self.rank_encs[source_state]
        target_enc = self.rank_encs[target_state]
        n = len(self.variables)

        # For each finite case j in source state q
        for fin_case_idx, fin_case in enumerate(source_enc.finite_cases):
            # For each infinity case k in target state q'
            for inf_case_idx, inf_case in enumerate(target_enc.infinity_cases):
                # Premise: A_i [x;x'] ≤ b_i
                A_s = prog_trans.A
                b_s = prog_trans.b

                # Middle premise: [P; C_j] x ≤ [r; d_j]
                # P: automaton transition guard
                # C_j: finite case guard from source state q
                # Need to expand from [x] to [x;x'] space
                P_exp, r_exp = self._align_and_expand(aut_trans.P, aut_trans.r, aut_trans.variables, primed=False)
                C_j_exp, d_j_exp = self._align_and_expand(fin_case.C_j, fin_case.d_j, source_enc.variables, primed=False)

                # Stack [P; C_j] and concatenate [r; d_j]
                if P_exp.shape[0] > 0 and C_j_exp.shape[0] > 0:
                    C = np.vstack([P_exp, C_j_exp])
                    d = np.concatenate([r_exp, d_j_exp])
                elif P_exp.shape[0] > 0:
                    C = P_exp
                    d = r_exp
                elif C_j_exp.shape[0] > 0:
                    C = C_j_exp
                    d = d_j_exp
                else:
                    C = np.zeros((0, 2*n), dtype=np.int64)
                    d = np.zeros(0, dtype=np.int64)

                # Conclusion: E_k x' > f_k (expand to [x;x'] space, check next state)
                # E_k: infinity case guard from target state q'
                E_exp, f_exp = self._align_and_expand(inf_case.E_k, inf_case.f_k, target_enc.variables, primed=True)

                # Build G_p = [C_p; E_p] - stack public middle premise with conclusion
                # A_s remains as program transition only (secret)
                # C is the middle premise [P; C_j] (public)
                # E_exp is the conclusion (public)
                if C.shape[0] > 0 and E_exp.shape[0] > 0:
                    G_p = np.vstack([C, E_exp])
                    h_p = np.concatenate([d, f_exp])
                elif C.shape[0] > 0:
                    G_p = C
                    h_p = d
                elif E_exp.shape[0] > 0:
                    G_p = E_exp
                    h_p = f_exp
                else:
                    G_p = np.zeros((0, 2*n), dtype=np.int64)
                    h_p = np.zeros(0, dtype=np.int64)

                # Build Farkas dual with separated secret (A_s) and public (G_p)
                dual = build_farkas_dual(A_s, b_s, G_p, h_p)

                # Solve
                sat, witness = solve_farkas_dual(dual)

                obligations.append(ObligationResult(
                    obligation_type="transition_non_infinity",
                    program_transition_idx=prog_idx,
                    automaton_transition=(aut_trans.from_state, aut_trans.to_state),
                    source_ranking_state=source_state,
                    target_ranking_state=target_state,
                    source_case_idx=fin_case_idx,
                    target_case_idx=None,
                    infinity_case_idx=inf_case_idx,
                    is_fair=aut_trans.is_fair,
                    passed=sat,
                    witness=witness if sat else None
                ))

        return obligations


    def _verify_update(
        self,
        prog_idx: int,
        prog_trans: TransitionEncoding,
        aut_trans: AutomatonTransitionEncoding
    ) -> List[ObligationResult]:
        """Type 3: Verify ranking decrease and non-negativity.

        For each finite case j (source) and k (target):
            A_i [x;x'] ≤ b_i => [P 0; C_j 0; 0 C_k] [x;x'] ≤ [r; d_j; d_k]
                              => [w_j, -w_k] [x;x'] > u_k - u_j + ζ

        This checks ranking decrease by ζ (1 for fair, 0 for non-fair).

        Args:
            prog_idx: Index of program transition
            prog_trans: Program transition encoding
            aut_trans: Automaton transition encoding

        Returns:
            List of ObligationResult (one per source_case × target_case pair)
        """
        obligations = []

        n = len(self.variables)
        zeta = 1 if aut_trans.is_fair else 0

        # Get source and target states from automaton transition
        source_state = aut_trans.from_state
        target_state = aut_trans.to_state

        # Skip if states don't have ranking functions
        if source_state not in self.rank_encs or target_state not in self.rank_encs:
            return obligations

        source_enc = self.rank_encs[source_state]
        target_enc = self.rank_encs[target_state]

        # For each finite case j in source
        for j, source_case in enumerate(source_enc.finite_cases):
            # For each finite case k in target
            for k, target_case in enumerate(target_enc.finite_cases):
                # Premise: A_i [x;x'] ≤ b_i
                A_s = prog_trans.A
                b_s = prog_trans.b

                # Middle premise: [P 0; C_j 0; 0 C_k] [x;x'] ≤ [r; d_j; d_k]
                P_exp, r_exp = self._align_and_expand(aut_trans.P, aut_trans.r, aut_trans.variables, primed=False)
                C_j_exp, d_j_exp = self._align_and_expand(source_case.C_j, source_case.d_j, source_enc.variables, primed=False)
                C_k_exp, d_k_exp = self._align_and_expand(target_case.C_j, target_case.d_j, target_enc.variables, primed=True)

                # Stack matrices
                matrices_to_stack = []
                vectors_to_concat = []

                if P_exp.shape[0] > 0:
                    matrices_to_stack.append(P_exp)
                    vectors_to_concat.append(r_exp)
                if C_j_exp.shape[0] > 0:
                    matrices_to_stack.append(C_j_exp)
                    vectors_to_concat.append(d_j_exp)
                if C_k_exp.shape[0] > 0:
                    matrices_to_stack.append(C_k_exp)
                    vectors_to_concat.append(d_k_exp)

                if matrices_to_stack:
                    C = np.vstack(matrices_to_stack)
                    d = np.concatenate(vectors_to_concat)
                else:
                    C = np.zeros((0, 2*n), dtype=np.int64)
                    d = np.zeros(0, dtype=np.int64)

                # Conclusion: [w_j, -w_k] [x;x'] > u_k - u_j + ζ
                # Build coefficient vector in [x;x'] space
                w_j_exp = np.zeros(2*n, dtype=np.int64)
                w_k_exp = np.zeros(2*n, dtype=np.int64)

                # Map source_case.w_j to x variables
                for var_idx, var in enumerate(source_enc.variables):
                    if var in self.variables:
                        full_idx = self.variables.index(var)
                        w_j_exp[full_idx] = source_case.w_j[var_idx]

                # Map target_case.w_j to x' variables
                for var_idx, var in enumerate(target_enc.variables):
                    if var in self.variables:
                        full_idx = self.variables.index(var)
                        w_k_exp[n + full_idx] = target_case.w_j[var_idx]

                E = (w_j_exp - w_k_exp).reshape(1, -1)  # Shape: (1, 2n)
                # For ranking decrease ≥ ζ, we need V_j - V_k > ζ - 1 (strict inequality for integers)
                f = np.array([target_case.u_j - source_case.u_j + zeta - 1], dtype=np.int64)

                # Build G_p = [C_p; E_p] - stack public middle premise with conclusion
                # A_s remains as program transition only (secret)
                # C is the middle premise [P; C_j; C_k] (public)
                # E is the conclusion (public)
                if C.shape[0] > 0 and E.shape[0] > 0:
                    G_p = np.vstack([C, E])
                    h_p = np.concatenate([d, f])
                elif C.shape[0] > 0:
                    G_p = C
                    h_p = d
                elif E.shape[0] > 0:
                    G_p = E
                    h_p = f
                else:
                    G_p = np.zeros((0, 2*n), dtype=np.int64)
                    h_p = np.zeros(0, dtype=np.int64)

                # Build Farkas dual with separated secret (A_s) and public (G_p)
                dual = build_farkas_dual(A_s, b_s, G_p, h_p)

                # Solve
                sat, witness = solve_farkas_dual(dual)

                obligations.append(ObligationResult(
                    obligation_type="update",
                    program_transition_idx=prog_idx,
                    automaton_transition=(source_state, target_state),
                    source_ranking_state=source_state,
                    target_ranking_state=target_state,
                    source_case_idx=j,
                    target_case_idx=k,
                    infinity_case_idx=None,
                    is_fair=aut_trans.is_fair,
                    passed=sat,
                    witness=witness if sat else None
                ))

        return obligations
