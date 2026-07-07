"""Automatic synthesis of ranking functions (Tier 1: single linear function per state).

Instead of hand-writing `rank(q): …`, synthesize one linear ranking `V(q,x) = w_q·x + u_q` per
automaton state so that the existing termination obligations hold:

  * decrease:        for every (program transition × automaton edge q→q'),
                     premise ⟹ V(q,x) − V(q',x') ≥ ζ      (ζ=1 on fair edges, 0 otherwise)
  * non-negativity:  region ⟹ V(q,x) ≥ 0

The template coefficients w_q, u_q are unknown. Each obligation is an implication
"conjunction of linear premises ⟹ a·y ≤ b"; by the affine form of Farkas' lemma it holds iff
there exist multipliers λ ≥ 0 with `a = λᵀP` and `λᵀq ≤ b`. Because the premise matrix P is a
fixed integer matrix and only the *conclusion* `a`/`b` carries the unknown coefficients, these
conditions are **linear** in (λ, w, u) — a single LP/LIA feasibility problem, solved with Z3
(Podelski–Rybalchenko). This is the reason the conclusion is used as the *target* of `a = λᵀP`
rather than stacked as an extra premise row (which would create bilinear λ·w terms).

The finite region per state is the variables' `type` box; the synthesized ranking is emitted as a
`RankingFunction` (one finite case over the box + a disjoint `inf` ladder covering the complement)
that the existing validator and verifier re-check. The synthesizer is therefore untrusted: a bug
can only cause a failed verification, never an unsound proof.

Tier 1 handles programs provable with a single linear function per state over a type-bounded
domain (e.g. counter-style loops). Piecewise / lexicographic synthesis is future work (Tier 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np
from numpy.typing import NDArray
import z3

from .ast_types import Comparison, CompOp, Expr, Var, Num, BinOp, TypeDef
from .ranking_types import RankingCase, RankingFunction
from .encoder import encode_transition, comparison_to_inequalities
from .parser import ParseResult


# Default bounds keep the LIA search finite and the coefficients inside the ZK field.
DEFAULT_COEFF_BOUND = 2**16
DEFAULT_MULTIPLIER_BOUND = 2**32 - 1


class SynthesisError(Exception):
    """Raised when no ranking of the requested class can be synthesized."""


# --------------------------------------------------------------------------------------
# Guard/region encoding helpers (x-space, aligned to a fixed variable ordering)
# --------------------------------------------------------------------------------------

def _guard_to_matrix(
    comparisons: List[Comparison],
    variables: List[str],
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Encode a conjunction of comparisons to (M, v) with M x ≤ v, in x-space (len(variables) cols)."""
    ineqs = []
    for comp in comparisons:
        ineqs.extend(comparison_to_inequalities(comp, primed=False))
    ineqs = [iq.to_nonstrict() for iq in ineqs]

    n = len(variables)
    idx = {v: i for i, v in enumerate(variables)}
    if not ineqs:
        return np.zeros((0, n), dtype=np.int64), np.zeros(0, dtype=np.int64)

    M = np.zeros((len(ineqs), n), dtype=np.int64)
    v = np.zeros(len(ineqs), dtype=np.int64)
    for i, iq in enumerate(ineqs):
        for var, coeff in iq.coeffs.items():
            if var in idx:
                M[i, idx[var]] = coeff
        v[i] = iq.const
    return M, v


def _type_box_comparisons(variables: List[str], types: Dict[str, TypeDef]) -> List[Comparison]:
    """The `type`-declared bounding box as comparisons (var ≥ lo, var ≤ hi) for typed variables."""
    box: List[Comparison] = []
    for var in variables:
        if var in types:
            td = types[var]
            box.append(Comparison(left=Var(var), right=Num(td.min_value), op=CompOp.GE))
            box.append(Comparison(left=Var(var), right=Num(td.max_value), op=CompOp.LE))
    return box


def _embed_x(M: NDArray[np.int64], n: int) -> NDArray[np.int64]:
    """Embed an x-space (m×n) matrix into [x;x'] space (m×2n) on the x columns."""
    if M.shape[0] == 0:
        return np.zeros((0, 2 * n), dtype=np.int64)
    return np.hstack([M, np.zeros((M.shape[0], n), dtype=np.int64)])


def _embed_xprime(M: NDArray[np.int64], n: int) -> NDArray[np.int64]:
    """Embed an x-space (m×n) matrix into [x;x'] space (m×2n) on the x' columns."""
    if M.shape[0] == 0:
        return np.zeros((0, 2 * n), dtype=np.int64)
    return np.hstack([np.zeros((M.shape[0], n), dtype=np.int64), M])


# --------------------------------------------------------------------------------------
# Affine-Farkas constraint emission
# --------------------------------------------------------------------------------------

def _add_farkas_implication(
    solver: z3.Solver,
    P: NDArray[np.int64],
    q: NDArray[np.int64],
    a_coeffs: list,   # list[z3 expr], length = P.shape[1]
    b_rhs,            # z3 expr
    lam_prefix: str,
    multiplier_bound: int,
) -> None:
    """Assert that {P y ≤ q} ⟹ (a·y ≤ b) via affine Farkas: ∃λ≥0. a = λᵀP ∧ λᵀq ≤ b.

    a_coeffs and b_rhs may contain the unknown template coefficients (Z3 exprs); P, q are integers.
    """
    m, ncols = P.shape
    assert len(a_coeffs) == ncols

    lam = [z3.Int(f"{lam_prefix}_{i}") for i in range(m)]
    for l in lam:
        solver.add(l >= 0)
        solver.add(l <= multiplier_bound)

    # a[col] == sum_i lam_i * P[i, col]
    for col in range(ncols):
        terms = [int(P[i, col]) * lam[i] for i in range(m) if int(P[i, col]) != 0]
        lhs = z3.Sum(terms) if terms else z3.IntVal(0)
        solver.add(a_coeffs[col] == lhs)

    # sum_i lam_i * q[i] <= b_rhs
    q_terms = [int(q[i]) * lam[i] for i in range(m) if int(q[i]) != 0]
    q_sum = z3.Sum(q_terms) if q_terms else z3.IntVal(0)
    solver.add(q_sum <= b_rhs)


# --------------------------------------------------------------------------------------
# Building the ranking-function AST from synthesized coefficients
# --------------------------------------------------------------------------------------

def _linear_expr(w: List[int], u: int, variables: List[str]) -> Expr:
    """Build an AST for w·x + u (skipping zero coefficients)."""
    expr: Expr = Num(int(u))
    for coeff, var in zip(w, variables):
        c = int(coeff)
        if c == 0:
            continue
        term: Expr = Var(var) if c == 1 else BinOp("*", Num(c), Var(var))
        expr = BinOp("+", expr, term)
    return expr


def _inf_ladder(variables: List[str], types: Dict[str, TypeDef]) -> List[RankingCase]:
    """Disjoint `inf` cases covering the complement of the type box (ladder over typed vars)."""
    cases: List[RankingCase] = []
    prefix: List[Comparison] = []
    for var in variables:
        if var not in types:
            continue
        td = types[var]
        below = Comparison(left=Var(var), right=Num(td.min_value), op=CompOp.LT)
        above = Comparison(left=Var(var), right=Num(td.max_value), op=CompOp.GT)
        cases.append(RankingCase(guards=prefix + [below], expression=None, is_infinity=True))
        cases.append(RankingCase(guards=prefix + [above], expression=None, is_infinity=True))
        prefix = prefix + [
            Comparison(left=Var(var), right=Num(td.min_value), op=CompOp.GE),
            Comparison(left=Var(var), right=Num(td.max_value), op=CompOp.LE),
        ]
    return cases


# --------------------------------------------------------------------------------------
# Main synthesis entry point
# --------------------------------------------------------------------------------------

def _collect_variables(result: ParseResult) -> List[str]:
    all_vars: set[str] = set()
    for cmd in result.commands or []:
        all_vars.update(cmd.get_variables())
    for trans in result.automaton_transitions or []:
        all_vars.update(trans.get_variables())
    if result.init_condition:
        for guard in result.init_condition:
            all_vars.update(_vars_of(guard.left) | _vars_of(guard.right))
    all_vars.update(result.types.keys())
    return sorted(all_vars)


def _vars_of(e: Expr) -> set[str]:
    if isinstance(e, Var):
        return {e.name}
    if isinstance(e, BinOp):
        return _vars_of(e.left) | _vars_of(e.right)
    from .ast_types import Neg
    if isinstance(e, Neg):
        return _vars_of(e.expr)
    return set()


def _automaton_states(result: ParseResult) -> List[str]:
    states: List[str] = []
    seen = set()
    for s in (result.automaton_initial_states or []):
        if s not in seen:
            seen.add(s); states.append(s)
    for t in result.automaton_transitions or []:
        for s in (t.from_state, t.to_state):
            if s not in seen:
                seen.add(s); states.append(s)
    return states


def synthesize_rankings(
    result: ParseResult,
    coeff_bound: int = DEFAULT_COEFF_BOUND,
    multiplier_bound: int = DEFAULT_MULTIPLIER_BOUND,
) -> Dict[str, RankingFunction]:
    """Synthesize a single linear ranking per automaton state (Tier 1).

    Returns a dict state -> RankingFunction. Raises SynthesisError if no such ranking exists
    (within the given coefficient bound) or if prerequisites are missing.
    """
    if not result.automaton_transitions:
        raise SynthesisError(
            "No automaton transitions: provide `trans(...)` or an LTL `spec:` before synthesizing."
        )

    variables = _collect_variables(result)
    n = len(variables)
    if n == 0:
        raise SynthesisError("No program variables found.")

    states = _automaton_states(result)
    types = result.types

    # Program transitions in [x;x'] space, aligned to `variables`.
    prog_encs = [
        encode_transition(cmd, variables, nonstrict_only=True, types=types)
        for cmd in (result.commands or [])
    ]

    # Per-state type-box region (x-space) and its [x;x'] embeddings.
    box_comps = _type_box_comparisons(variables, types)
    box_M, box_v = _guard_to_matrix(box_comps, variables)      # (mb, n)
    box_x = _embed_x(box_M, n)                                  # source region on x
    box_xp = _embed_xprime(box_M, n)                            # target region on x'

    solver = z3.Solver()

    # Symbolic template coefficients per state.
    w = {q: [z3.Int(f"w_{q}_{i}") for i in range(n)] for q in states}
    u = {q: z3.Int(f"u_{q}") for q in states}
    for q in states:
        for c in w[q]:
            solver.add(c >= -coeff_bound, c <= coeff_bound)
        solver.add(u[q] >= -coeff_bound, u[q] <= coeff_bound)

    # Non-negativity: box(x) ⟹ w_q·x + u_q ≥ 0, i.e. (-w_q)·x ≤ u_q.
    for q in states:
        a = [-w[q][c] for c in range(n)]
        _add_farkas_implication(
            solver, box_M, box_v, a, u[q],
            lam_prefix=f"nn_{q}", multiplier_bound=multiplier_bound,
        )

    # Decrease: for each (program transition × automaton edge q→q'),
    # premise ⟹ w_q·x − w_q'·x' ≥ ζ + u_q' − u_q, i.e. a·y ≤ (u_q − u_q' − ζ)
    # with a = [−w_q on x cols, +w_q' on x' cols].
    aut = result.automaton_transitions
    for p_idx, prog in enumerate(prog_encs):
        # program transition premise, [x;x'] space
        A = prog.A  # (m, 2n)
        b = prog.b
        for a_idx, edge in enumerate(aut):
            q, qp = edge.from_state, edge.to_state
            zeta = 1 if edge.is_fair else 0
            # automaton guard on x
            P_aut, r_aut = _guard_to_matrix(edge.guards, variables)
            P_aut_e = _embed_x(P_aut, n)
            # stack premise: program transition + automaton guard + source box(x) + target box(x')
            blocks = [A, P_aut_e, box_x, box_xp]
            rhss = [b, r_aut, box_v, box_v]
            blocks = [B for B in blocks if B.shape[0] > 0]
            rhss = [r for B, r in zip([A, P_aut_e, box_x, box_xp], [b, r_aut, box_v, box_v]) if B.shape[0] > 0]
            if blocks:
                P = np.vstack(blocks)
                qv = np.concatenate(rhss)
            else:
                P = np.zeros((0, 2 * n), dtype=np.int64)
                qv = np.zeros(0, dtype=np.int64)

            a_coeffs = [-w[q][c] for c in range(n)] + [w[qp][c] for c in range(n)]
            b_rhs = u[q] - u[qp] - zeta
            _add_farkas_implication(
                solver, P, qv, a_coeffs, b_rhs,
                lam_prefix=f"dec_{p_idx}_{a_idx}", multiplier_bound=multiplier_bound,
            )

    if solver.check() != z3.sat:
        raise SynthesisError(
            "No single linear ranking function per state exists within the coefficient bound "
            f"({coeff_bound}). The program may need a piecewise/lexicographic ranking (Tier 2) "
            "or the variables may lack `type` bounds."
        )

    model = solver.model()
    rankings: Dict[str, RankingFunction] = {}
    inf_cases = _inf_ladder(variables, types)
    for q in states:
        w_vals = [model.eval(w[q][c], model_completion=True).as_long() for c in range(n)]
        u_val = model.eval(u[q], model_completion=True).as_long()
        finite = RankingCase(
            guards=list(box_comps),
            expression=_linear_expr(w_vals, u_val, variables),
            is_infinity=False,
        )
        rankings[q] = RankingFunction(state=q, cases=[finite] + list(inf_cases))
    return rankings


def synthesize_into(result: ParseResult, **kwargs) -> ParseResult:
    """Synthesize rankings for states that don't already have one; fill them into `result`."""
    synthesized = synthesize_rankings(result, **kwargs)
    for state, rf in synthesized.items():
        result.ranking_functions.setdefault(state, rf)
    return result
