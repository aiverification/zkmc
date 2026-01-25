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
from .farkas import build_farkas_dual_disjunctive
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
        self.trans_encs = encode_program(result.commands, nonstrict_only=True) if result.commands else []
        self.aut_encs = encode_automaton_transitions(result.automaton_transitions) if result.automaton_transitions else []

        # Encode ranking functions with the full variable list
        from .ranking_encoder import encode_ranking_function
        self.rank_encs = {
            state: encode_ranking_function(rf, self.variables)
            for state, rf in result.ranking_functions.items()
        }

        # Encode init condition with the full variable list
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

        New verification system with disjunctive obligations:
        - Initial: One per state (checks all ranking cases via disjunction)
        - Update: One per (prog_trans, aut_trans, source_case) triple

        Returns:
            VerificationResult with all obligations checked
        """
        obligations = []

        # 1. Initial condition obligations (one per state)
        obligations.extend(self._verify_initial_disjunctive())

        # 2. Update obligations (one per program transition × automaton transition × source case)
        for prog_idx, prog_trans in enumerate(self.trans_encs):
            for aut_trans in self.aut_encs:
                obligations.extend(
                    self._verify_update_disjunctive(prog_idx, prog_trans, aut_trans)
                )

        return VerificationResult(
            passed=all(o.passed for o in obligations),
            obligations=obligations
        )

    def _verify_initial_disjunctive(self) -> List[ObligationResult]:
        """Verify initial condition obligations with disjunctive conclusion.

        For each automaton state q with ranking function:
            A_0 x ≤ b_0 ⟹ ∨_{k=1}^m [W_k^(q) x > -u_k^(q) - 1 ∧ C_k^(q) x ≤ d_k^(q)]

        In code notation, for each case k (encoded as A_j, b_j, C_j, d_j):
            E_k = [C_j; -A_j] (ranking non-negative; guard satisfied)
            f_k = [-d_j - 1; -b_j - 1] (element-wise -1 for guard)

        This checks that at initial states:
        1. At least one ranking case is satisfied (guard)
        2. That case gives non-negative ranking value

        Returns:
            List of ObligationResult (one per state with ranking function)
        """
        obligations = []

        # If no initial condition, skip
        if not self.init_enc:
            return obligations

        # For each state with a ranking function
        for state, rank_enc in self.rank_encs.items():
            # Check if ranking function has any cases
            if len(rank_enc.cases) == 0:
                # No cases - ranking function is always undefined
                obligations.append(ObligationResult(
                    obligation_type="initial",
                    program_transition_idx=None,
                    automaton_transition=None,
                    source_ranking_state=state,
                    target_ranking_state=None,
                    source_case_idx=None,
                    is_fair=False,
                    passed=False,
                    witness=None
                ))
                continue

            # Build premise: A_0 x ≤ b_0
            A_s = self.init_enc.A_0
            b_s = self.init_enc.b_0

            # Build disjunctive conclusion: one E_k, f_k for each ranking case
            E_list = []
            f_list = []

            n = len(self.variables)

            for case in rank_enc.cases:
                # Paper notation: V(x,q) = W_k x + u_k if C_k x ≤ d_k
                C_k = case.C_j  # Guard matrix: C_k x ≤ d_k [shape: (m_k, n)]
                d_k = case.d_j  # Guard vector [shape: (m_k,)]
                W_k = case.W_j  # Expression coeffs: W_k x + u_k [shape: (n,) - 1D!]
                u_k = case.u_j  # Expression constant [scalar]

                # Reshape W_k to be 2D (1, n) for stacking
                W_k_row = W_k.reshape(1, -1)  # Shape: (1, n)

                # Build E_k: stack [W_k; -C_k]
                # Row 1: W_k x > -u_k - 1 (ranking non-negative: W_k x + u_k ≥ 0)
                # Rows 2+: -C_k x > -d_k - 1 (guard satisfied: C_k x ≤ d_k)
                E_k = np.vstack([
                    W_k_row,     # Shape: (1, n)
                    -C_k         # Shape: (len(d_k), n)
                ])

                # Build f_k with element-wise -1 offsets
                f_k = np.concatenate([
                    np.array([-u_k - 1], dtype=np.int64),        # Row 1
                    -d_k - np.ones(len(d_k), dtype=np.int64)     # Rows 2+ (element-wise -1)
                ])

                E_list.append(E_k)
                f_list.append(f_k)

            # No middle premise for initial obligation
            C = None
            d = None

            # Build Farkas dual
            dual = build_farkas_dual_disjunctive(A_s, b_s, C, d, E_list, f_list)

            # Solve
            sat, witness = solve_farkas_dual(dual)

            obligations.append(ObligationResult(
                obligation_type="initial",
                program_transition_idx=None,
                automaton_transition=None,
                source_ranking_state=state,
                target_ranking_state=None,
                source_case_idx=None,
                is_fair=False,
                passed=sat,
                witness=witness
            ))

        return obligations

    def _verify_update_disjunctive(
        self,
        prog_idx: int,
        prog_trans: TransitionEncoding,
        aut_trans: AutomatonTransitionEncoding
    ) -> List[ObligationResult]:
        """Verify update obligations with disjunctive conclusion.

        For each source ranking case j, checks:
            A_i [x;x'] ≤ b_i ⟹ [P^(σ) x ≤ r^(σ) ∧ C_j^(q) x ≤ d_j^(q)] ⟹
              ∨_{k=1}^m [V(x,q) - V(x',q') ≥ ζ ∧ V(x',q') ≥ 0 ∧ C_k^(q') x' ≤ d_k^(q')]

        Where ζ = 1 if fair transition, else 0.

        Args:
            prog_idx: Index of program transition
            prog_trans: Program transition encoding
            aut_trans: Automaton transition encoding

        Returns:
            List of ObligationResult (one per source ranking case)
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

        # Check if ranking functions have cases
        if len(rank_from.cases) == 0 or len(rank_to.cases) == 0:
            return obligations

        n = len(self.variables)

        # Iterate over all source cases j
        for case_idx, case_from in enumerate(rank_from.cases):
            # Build three-part implication:
            # A_i [x;x'] ≤ b_i ⟹ [P^(σ) x ≤ r^(σ) ∧ C_j^(q) x ≤ d_j^(q)] ⟹ ∨_k [...]

            # 1. First premise A_s: Program transition (already in [x, x'] space)
            A_s = prog_trans.A
            b_s = prog_trans.b

            # 2. Middle premise C, d: Automaton guard + Source ranking guard

            # 2a. Automaton transition guard P^(σ) x ≤ r^(σ) (expand from [x] to [x, x'], unprimed)
            P_sigma_exp, r_sigma_exp = self._align_and_expand(
                aut_trans.P, aut_trans.r, aut_trans.variables, primed=False
            )

            # 2b. Source ranking guard C_j^(q) x ≤ d_j^(q) (expand from [x] to [x, x'], unprimed)
            C_j_guard = case_from.C_j  # Guard matrix for source case j
            d_j_guard = case_from.d_j  # Guard vector for source case j
            C_j_guard_exp, d_j_guard_exp = self._align_and_expand(
                C_j_guard, d_j_guard, rank_from.variables, primed=False
            )

            # Stack middle premise constraints
            C = np.vstack([P_sigma_exp, C_j_guard_exp])
            d = np.concatenate([r_sigma_exp, d_j_guard_exp])

            # Build disjunctive conclusion: one E_k, f_k for each target ranking case k
            E_list = []
            f_list = []

            # ζ parameter: 1 for fair transitions, 0 for non-fair
            zeta = 1 if aut_trans.is_fair else 0

            # Get source case j encodings (paper notation)
            # Paper: source case j is V(x,q) = W_j x + u_j if C_j x ≤ d_j
            W_j = case_from.W_j        # W_j^(q) expression coeffs [shape: (n,) - 1D!]
            u_j = case_from.u_j        # u_j^(q) expression constant [scalar]

            # Reshape W_j to be 2D for matrix operations
            W_j_row = W_j.reshape(1, -1)  # Shape: (1, n)

            for case_to in rank_to.cases:
                # Get target case k encodings (paper notation)
                # Paper: target case k is V(x',q') = W_k x' + u_k if C_k x' ≤ d_k
                C_k_guard = case_to.C_j  # C_k^(q') guard matrix [shape: (m_k, n)]
                d_k_guard = case_to.d_j  # d_k^(q') guard vector
                W_k = case_to.W_j        # W_k^(q') expression coeffs [shape: (n,) - 1D!]
                u_k = case_to.u_j        # u_k^(q') expression constant [scalar]

                # Reshape W_k to be 2D for matrix operations
                W_k_row = W_k.reshape(1, -1)  # Shape: (1, n)

                # Build E_k matrix in [x;x'] space (2n variables)
                # Paper formulation for case k:
                # Row 1: W_j x - W_k x' > u_k - u_j + ζ - 1  (ranking decrease by ζ)
                # Row 2: W_k x' > -u_k - 1  (target non-negative: W_k x' + u_k ≥ 0)
                # Rows 3+: -C_k x' > -d_k - 1  (target guard satisfied: C_k x' ≤ d_k)

                E_k = np.vstack([
                    # Row 1: [W_j, -W_k] in [x;x'] space
                    np.hstack([W_j_row, -W_k_row]),              # Shape: (1, 2n)
                    # Row 2: [0, W_k] in [x;x'] space
                    np.hstack([np.zeros((1, n), dtype=np.int64), W_k_row]),  # Shape: (1, 2n)
                    # Rows 3+: [0, -C_k] in [x;x'] space
                    np.hstack([np.zeros((len(d_k_guard), n), dtype=np.int64), -C_k_guard])  # Shape: (m_k, 2n)
                ])

                # Build f_k vector with element-wise -1 offsets
                # Row 1: W_j x - W_k x' > u_k - u_j + ζ - 1
                #        which is: (W_j x + u_j) - (W_k x' + u_k) ≥ ζ for integers
                # Row 2: W_k x' > -u_k - 1  (i.e., W_k x' + u_k ≥ 0)
                # Rows 3+: -C_k x' > -d_k - 1  (i.e., C_k x' ≤ d_k)
                f_k = np.concatenate([
                    np.array([u_k - u_j + zeta - 1], dtype=np.int64),       # Row 1: decrease bound
                    np.array([-u_k - 1], dtype=np.int64),                   # Row 2: non-negative
                    -d_k_guard - np.ones(len(d_k_guard), dtype=np.int64)    # Rows 3+: guard satisfied
                ])

                E_list.append(E_k)
                f_list.append(f_k)

            # Build Farkas dual with three-part implication
            dual = build_farkas_dual_disjunctive(A_s, b_s, C, d, E_list, f_list)

            # Solve
            sat, witness = solve_farkas_dual(dual)

            obligations.append(ObligationResult(
                obligation_type="update",
                program_transition_idx=prog_idx,
                automaton_transition=(from_state, to_state),
                source_ranking_state=from_state,
                target_ranking_state=to_state,
                source_case_idx=case_idx,
                is_fair=aut_trans.is_fair,
                passed=sat,
                witness=witness
            ))

        return obligations

    def _get_obligation_matrices(self, obl_result: ObligationResult) -> dict:
        """Reconstruct matrices for an obligation result.

        New disjunctive format returns:
            - A_s, b_s: First premise
            - C, d: Middle premise (empty for initial obligations)
            - E_list, f_list: Disjunctive conclusions (list of matrices/vectors)

        Returns:
            Dictionary with keys: A_s, b_s, C, d, E_list, f_list
        """
        n = len(self.variables)

        if obl_result.obligation_type == "initial":
            # Initial obligation: A_0 x ≤ b_0 ⟹ ∨_k [W_k x > -u_k - 1 ∧ C_k x ≤ d_k]
            state = obl_result.source_ranking_state
            rank_enc = self.rank_encs[state]

            # Premise
            A_s = self.init_enc.A_0
            b_s = self.init_enc.b_0

            # Middle premise (empty for initial)
            C = np.zeros((0, n), dtype=np.int64)
            d = np.zeros(0, dtype=np.int64)

            # Disjunctive conclusion: one E_k, f_k per ranking case
            E_list = []
            f_list = []

            for case in rank_enc.cases:
                # Get case encodings (paper notation)
                C_k = case.C_j  # Guard matrix
                d_k = case.d_j  # Guard vector
                W_k = case.W_j  # Expression coeffs
                u_k = case.u_j  # Expression constant

                # Build E_k = [W_k; -C_k]
                W_k_row = W_k.reshape(1, -1)  # Shape: (1, n)
                E_k = np.vstack([W_k_row, -C_k])

                # Build f_k = [-u_k - 1; -d_k - ones]
                f_k = np.concatenate([
                    np.array([-u_k - 1], dtype=np.int64),
                    -d_k - np.ones(len(d_k), dtype=np.int64)
                ])

                E_list.append(E_k)
                f_list.append(f_k)

            return {
                "A_s": A_s,
                "b_s": b_s,
                "C": C,
                "d": d,
                "E_list": E_list,
                "f_list": f_list
            }

        elif obl_result.obligation_type == "update":
            # Update obligation: A_i [x;x'] ≤ b_i ⟹ [P^(σ) x ≤ r^(σ) ∧ C_j x ≤ d_j] ⟹ ∨_k [...]
            prog_idx = obl_result.program_transition_idx
            from_state, to_state = obl_result.automaton_transition
            source_case_idx = obl_result.source_case_idx

            # Get encodings
            prog_trans = self.trans_encs[prog_idx]
            aut_trans = next(a for a in self.aut_encs
                           if a.from_state == from_state and a.to_state == to_state)
            rank_from = self.rank_encs[from_state]
            rank_to = self.rank_encs[to_state]
            case_from = rank_from.cases[source_case_idx]

            # 1. First premise A_s: Program transition (already in [x, x'] space)
            A_s = prog_trans.A
            b_s = prog_trans.b

            # 2. Middle premise C, d: Automaton guard + Source ranking guard

            # 2a. Automaton transition guard P^(σ) x ≤ r^(σ) (expand to [x, x'], unprimed)
            P_sigma_exp, r_sigma_exp = self._align_and_expand(
                aut_trans.P, aut_trans.r, aut_trans.variables, primed=False
            )

            # 2b. Source ranking guard C_j^(q) x ≤ d_j^(q) (expand to [x, x'], unprimed)
            C_j_guard = case_from.C_j
            d_j_guard = case_from.d_j
            C_j_guard_exp, d_j_guard_exp = self._align_and_expand(
                C_j_guard, d_j_guard, rank_from.variables, primed=False
            )

            # Stack middle premise
            C = np.vstack([P_sigma_exp, C_j_guard_exp])
            d = np.concatenate([r_sigma_exp, d_j_guard_exp])

            # 3. Disjunctive conclusion: one E_k, f_k per target ranking case
            E_list = []
            f_list = []

            zeta = 1 if aut_trans.is_fair else 0

            # Get source case encodings
            W_j = case_from.W_j
            u_j = case_from.u_j
            W_j_row = W_j.reshape(1, -1)

            for case_to in rank_to.cases:
                # Get target case k encodings
                C_k_guard = case_to.C_j
                d_k_guard = case_to.d_j
                W_k = case_to.W_j
                u_k = case_to.u_j
                W_k_row = W_k.reshape(1, -1)

                # Build E_k in [x;x'] space (2n variables)
                E_k = np.vstack([
                    np.hstack([W_j_row, -W_k_row]),  # Row 1: ranking decrease
                    np.hstack([np.zeros((1, n), dtype=np.int64), W_k_row]),  # Row 2: target ≥ 0
                    np.hstack([np.zeros((len(d_k_guard), n), dtype=np.int64), -C_k_guard])  # Rows 3+: target guard
                ])

                # Build f_k
                f_k = np.concatenate([
                    np.array([u_k - u_j + zeta - 1], dtype=np.int64),
                    np.array([-u_k - 1], dtype=np.int64),
                    -d_k_guard - np.ones(len(d_k_guard), dtype=np.int64)
                ])

                E_list.append(E_k)
                f_list.append(f_k)

            return {
                "A_s": A_s,
                "b_s": b_s,
                "C": C,
                "d": d,
                "E_list": E_list,
                "f_list": f_list
            }

        else:
            raise ValueError(f"Unknown obligation type: {obl_result.obligation_type}")
