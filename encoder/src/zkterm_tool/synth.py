"""Automatic synthesis of ranking functions.

Tier 1 synthesizes a single linear function ``V(q,x) = w·x + u`` per automaton state; Tier 2
generalizes this to a **piecewise** ranking by partitioning the state space into regions and
synthesizing one linear piece per (state × region). The template coefficients are unknown; each
obligation "premises ⟹ a·y ≤ b" is discharged by the affine form of Farkas' lemma, which — because
the premise matrix is a fixed integer matrix and only the conclusion carries the unknowns — is
**linear** in (λ, w, u), i.e. an LP/LIA feasibility problem solved with Z3 (Podelski–Rybalchenko).
The conclusion is used as the *target* of ``a = λᵀP`` rather than stacked as a premise row (which
would create bilinear λ·w terms).

Partitioning uses **control-flow refinement**: each variable is split by the constants it is compared
against in the program guards, automaton guards, and init condition (so ``delay`` compared only to
``0`` yields two regions, not its whole ``0..255`` domain). The search tries partitions
coarsest-first (fewest regions), returning the first feasible one, so the ranking has as few finite
cases as possible — the number of ``update`` obligations (hence ZK proof size) grows with the square
of the case count. Tier 1 is exactly the empty-partition case.

**Reachability invariants:** for bounded programs, the reachable states are enumerated by a BFS
fixpoint from the initial condition, and each region's finite piece is guarded by the *bounding box
of reachable states in that region* rather than the full type box. This encodes conditional
invariants (e.g. "state1 ≤ 1 when turn == 0") that programs like round-robin need, and marks
unreachable regions ``inf``. Everything outside a region's reachable box is covered by a disjoint
``inf`` ladder, so the emitted ranking is well-formed.

The synthesizer is untrusted: its output is re-checked by the existing validator and verifier, so a
bug can only cause a failed verification, never an unsound proof.
"""

from __future__ import annotations

from itertools import combinations, product
from typing import Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
from numpy.typing import NDArray
import z3

from .ast_types import Comparison, CompOp, Expr, Var, Num, BinOp, Neg, TypeDef
from .ranking_types import RankingCase, RankingFunction
from .encoder import encode_transition, comparison_to_inequalities, expr_to_linear
from .parser import ParseResult


DEFAULT_COEFF_BOUND = 2**16
DEFAULT_MULTIPLIER_BOUND = 2**32 - 1
DEFAULT_MAX_REGIONS = 64
DEFAULT_MAX_MODE_VARS = 2
DEFAULT_REACH_BUDGET = 200_000  # max |type box| to enumerate reachable states


class SynthesisError(Exception):
    """Raised when no ranking of the requested class can be synthesized."""


# A region fixes each chosen mode variable to an integer interval [a, b].
Region = Dict[str, Tuple[int, int]]
# A box gives [lo, hi] bounds per (non-mode) variable; None marks an empty (unreachable) region.
Box = Optional[Dict[str, Tuple[int, int]]]


# --------------------------------------------------------------------------------------
# Guard encoding helpers (aligned to a fixed variable ordering)
# --------------------------------------------------------------------------------------

def _guard_to_matrix(
    comparisons: List[Comparison], variables: List[str],
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


def _embed_x(M: NDArray[np.int64], n: int) -> NDArray[np.int64]:
    if M.shape[0] == 0:
        return np.zeros((0, 2 * n), dtype=np.int64)
    return np.hstack([M, np.zeros((M.shape[0], n), dtype=np.int64)])


def _embed_xprime(M: NDArray[np.int64], n: int) -> NDArray[np.int64]:
    if M.shape[0] == 0:
        return np.zeros((0, 2 * n), dtype=np.int64)
    return np.hstack([np.zeros((M.shape[0], n), dtype=np.int64), M])


def _stack(blocks: List[NDArray[np.int64]], rhss: List[NDArray[np.int64]], ncols: int):
    kept = [(B, r) for B, r in zip(blocks, rhss) if B.shape[0] > 0]
    if kept:
        return np.vstack([B for B, _ in kept]), np.concatenate([r for _, r in kept])
    return np.zeros((0, ncols), dtype=np.int64), np.zeros(0, dtype=np.int64)


# --------------------------------------------------------------------------------------
# Affine-Farkas constraint emission
# --------------------------------------------------------------------------------------

def _add_farkas_implication(
    solver, P: NDArray[np.int64], q: NDArray[np.int64],
    a_coeffs: list, b_rhs, lam_prefix: str, multiplier_bound: int,
) -> None:
    """Assert {P y ≤ q} ⟹ (a·y ≤ b) via affine Farkas: ∃λ≥0. a = λᵀP ∧ λᵀq ≤ b."""
    m, ncols = P.shape
    assert len(a_coeffs) == ncols
    lam = [z3.Int(f"{lam_prefix}_{i}") for i in range(m)]
    for l in lam:
        solver.add(l >= 0)
        solver.add(l <= multiplier_bound)
    for col in range(ncols):
        terms = [int(P[i, col]) * lam[i] for i in range(m) if int(P[i, col]) != 0]
        solver.add(a_coeffs[col] == (z3.Sum(terms) if terms else z3.IntVal(0)))
    q_terms = [int(q[i]) * lam[i] for i in range(m) if int(q[i]) != 0]
    solver.add((z3.Sum(q_terms) if q_terms else z3.IntVal(0)) <= b_rhs)


def _feasible(P: NDArray[np.int64], q: NDArray[np.int64]) -> bool:
    """Is {y : P y ≤ q} non-empty? Used to prune vacuous cross-region transitions."""
    if P.shape[0] == 0:
        return True
    s = z3.Solver()
    y = [z3.Int(f"y{i}") for i in range(P.shape[1])]
    for i in range(P.shape[0]):
        terms = [int(P[i, c]) * y[c] for c in range(P.shape[1]) if int(P[i, c]) != 0]
        s.add((z3.Sum(terms) if terms else z3.IntVal(0)) <= int(q[i]))
    return s.check() == z3.sat


# --------------------------------------------------------------------------------------
# Program inspection: variables, states, expression evaluation
# --------------------------------------------------------------------------------------

def _vars_of(e: Expr) -> set[str]:
    if isinstance(e, Var):
        return {e.name}
    if isinstance(e, BinOp):
        return _vars_of(e.left) | _vars_of(e.right)
    if isinstance(e, Neg):
        return _vars_of(e.expr)
    return set()


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


def _automaton_states(result: ParseResult) -> List[str]:
    states: List[str] = []
    seen: set[str] = set()
    for s in (result.automaton_initial_states or []):
        if s not in seen:
            seen.add(s); states.append(s)
    for t in result.automaton_transitions or []:
        for s in (t.from_state, t.to_state):
            if s not in seen:
                seen.add(s); states.append(s)
    return states


def _eval_expr(expr: Expr, state: Dict[str, int]) -> int:
    le = expr_to_linear(expr)
    return sum(c * state[v] for v, c in le.coeffs.items()) + le.const


def _eval_comparison(comp: Comparison, state: Dict[str, int]) -> bool:
    lhs = _eval_expr(comp.left, state) - _eval_expr(comp.right, state)
    op = comp.op
    if op == CompOp.LT: return lhs < 0
    if op == CompOp.LE: return lhs <= 0
    if op == CompOp.EQ: return lhs == 0
    if op == CompOp.GE: return lhs >= 0
    if op == CompOp.GT: return lhs > 0
    raise ValueError(f"Unknown operator {op}")


# --------------------------------------------------------------------------------------
# Reachability (BFS fixpoint over the type box)
# --------------------------------------------------------------------------------------

def _reachable_states(
    result: ParseResult, variables: List[str], types: Dict[str, TypeDef], budget: int,
) -> Optional[Set[tuple]]:
    """Enumerate reachable states (as ordered tuples) within the type box, or None if not enumerable
    (an untyped variable, or a type box larger than `budget`)."""
    if any(v not in types for v in variables):
        return None
    ranges = [range(types[v].min_value, types[v].max_value + 1) for v in variables]
    size = 1
    for r in ranges:
        size *= len(r)
        if size > budget:
            return None

    def in_box(state: Dict[str, int]) -> bool:
        return all(types[v].min_value <= state[v] <= types[v].max_value for v in variables)

    def sat(state: Dict[str, int], comps: List[Comparison]) -> bool:
        return all(_eval_comparison(c, state) for c in comps)

    init_comps = result.init_condition or []
    reachable: Set[tuple] = set()
    frontier: List[Dict[str, int]] = []
    for values in product(*ranges):
        state = dict(zip(variables, values))
        if sat(state, init_comps):
            reachable.add(values)
            frontier.append(state)

    commands = result.commands or []
    while frontier:
        state = frontier.pop()
        for cmd in commands:
            if not sat(state, cmd.guards):
                continue
            ns = dict(state)
            for a in cmd.assignments:
                ns[a.var] = _eval_expr(a.expr, state)
            if in_box(ns):
                t = tuple(ns[v] for v in variables)
                if t not in reachable:
                    reachable.add(t)
                    frontier.append(ns)
    return reachable


# --------------------------------------------------------------------------------------
# Split points, partitions, regions, boxes
# --------------------------------------------------------------------------------------

def _comparison_threshold(comp: Comparison) -> Optional[Tuple[str, int]]:
    """If `comp` is a single-variable comparison, return (var, integer boundary), else None."""
    diff = expr_to_linear(comp.left) - expr_to_linear(comp.right)  # left - right OP 0
    nz = {v: c for v, c in diff.coeffs.items() if c != 0}
    if len(nz) != 1:
        return None
    v, c = next(iter(nz.items()))
    num = -diff.const
    if c == 0 or num % c != 0:
        return None
    return v, num // c


def _guard_split_points(result: ParseResult) -> Dict[str, set]:
    splits: Dict[str, set] = {}

    def scan(comps: List[Comparison]) -> None:
        for comp in comps:
            hit = _comparison_threshold(comp)
            if hit is not None:
                splits.setdefault(hit[0], set()).add(hit[1])

    for cmd in result.commands or []:
        scan(cmd.guards)
    for trans in result.automaton_transitions or []:
        scan(trans.guards)
    if result.init_condition:
        scan(result.init_condition)
    return splits


def _intervals(points: set, td: TypeDef) -> List[Tuple[int, int]]:
    """Partition [lo, hi] into integer intervals induced by the split points."""
    lo, hi = td.min_value, td.max_value
    pts = sorted(p for p in points if lo <= p <= hi)
    intervals: List[Tuple[int, int]] = []
    prev = lo
    for c in pts:
        if prev <= c - 1:
            intervals.append((prev, c - 1))
        intervals.append((c, c))
        prev = c + 1
    if prev <= hi:
        intervals.append((prev, hi))
    return intervals


def _mode_partitions(
    variables: List[str], var_intervals: Dict[str, List[Tuple[int, int]]],
    max_regions: int, max_mode_vars: int,
) -> List[List[str]]:
    """Candidate mode-variable subsets, coarsest-first (fewest resulting regions). Empty = Tier 1."""
    useful = [v for v in variables if len(var_intervals.get(v, [])) > 1]
    subsets: List[Tuple[int, List[str]]] = [(1, [])]
    for k in range(1, max_mode_vars + 1):
        for combo in combinations(useful, k):
            regions = 1
            for v in combo:
                regions *= len(var_intervals[v])
            if regions <= max_regions:
                subsets.append((regions, list(combo)))
    subsets.sort(key=lambda t: (t[0], len(t[1])))
    return [s for _, s in subsets]


def _regions_of(subset: List[str], var_intervals: Dict[str, List[Tuple[int, int]]]) -> List[Region]:
    if not subset:
        return [{}]
    return [dict(zip(subset, combo)) for combo in product(*[var_intervals[v] for v in subset])]


def _region_box(
    region: Region, subset: List[str], variables: List[str], types: Dict[str, TypeDef],
    reachable: Optional[Set[tuple]],
) -> Box:
    """Bounding box (per non-mode typed var) of reachable states in `region`; None if the region has
    no reachable states. Falls back to the full type box when reachability is unavailable."""
    nonmode_typed = [v for v in variables if v not in region and v in types]
    if reachable is None:
        return {v: (types[v].min_value, types[v].max_value) for v in nonmode_typed}

    idx = {v: i for i, v in enumerate(variables)}
    in_region = [
        s for s in reachable
        if all(region[m][0] <= s[idx[m]] <= region[m][1] for m in subset)
    ]
    if not in_region:
        return None
    box: Dict[str, Tuple[int, int]] = {}
    for v in nonmode_typed:
        vals = [s[idx[v]] for s in in_region]
        box[v] = (min(vals), max(vals))
    return box


def _region_mode_comparisons(region: Region) -> List[Comparison]:
    comps: List[Comparison] = []
    for v, (a, b) in region.items():
        comps.append(Comparison(left=Var(v), right=Num(a), op=CompOp.GE))
        comps.append(Comparison(left=Var(v), right=Num(b), op=CompOp.LE))
    return comps


def _finite_guard(region: Region, box: Dict[str, Tuple[int, int]], variables: List[str]) -> List[Comparison]:
    """Finite-case guard: mode-var intervals ∧ reachable-box bounds on the non-mode vars."""
    comps = _region_mode_comparisons(region)
    for v in variables:
        if v in box:
            lo, hi = box[v]
            comps.append(Comparison(left=Var(v), right=Num(lo), op=CompOp.GE))
            comps.append(Comparison(left=Var(v), right=Num(hi), op=CompOp.LE))
    return comps


# --------------------------------------------------------------------------------------
# Ranking-function AST construction
# --------------------------------------------------------------------------------------

def _linear_expr(w: Sequence[int], u: int, variables: List[str]) -> Expr:
    expr: Expr = Num(int(u))
    for coeff, var in zip(w, variables):
        c = int(coeff)
        if c == 0:
            continue
        term: Expr = Var(var) if c == 1 else BinOp("*", Num(c), Var(var))
        expr = BinOp("+", expr, term)
    return expr


def _box_ladder(
    prefix: List[Comparison], order: List[str], bounds: Dict[str, Tuple[int, int]],
) -> List[RankingCase]:
    """Disjoint `inf` cases covering the complement of `bounds` (a box over `order`) within `prefix`."""
    cases: List[RankingCase] = []
    running = list(prefix)
    for v in order:
        lo, hi = bounds[v]
        cases.append(RankingCase(running + [Comparison(Var(v), Num(lo), CompOp.LT)], None, True))
        cases.append(RankingCase(running + [Comparison(Var(v), Num(hi), CompOp.GT)], None, True))
        running = running + [
            Comparison(Var(v), Num(lo), CompOp.GE),
            Comparison(Var(v), Num(hi), CompOp.LE),
        ]
    return cases


# --------------------------------------------------------------------------------------
# Per-partition LP
# --------------------------------------------------------------------------------------

def _solve_partition(
    result: ParseResult, variables: List[str], states: List[str], prog_encs: list,
    regions: List[Region], boxes: List[Box], region_guards: List[Optional[List[Comparison]]],
    coeff_bound: int, multiplier_bound: int,
) -> Optional[Dict[Tuple[str, int], Tuple[List[int], int]]]:
    """Solve the affine-Farkas LP for a fixed partition. Empty regions (box None) get no piece.
    Returns {(state, region_idx): (w, u)} for non-empty regions, or None if infeasible."""
    n = len(variables)
    live = [ri for ri in range(len(regions)) if boxes[ri] is not None]

    region_M: Dict[int, NDArray[np.int64]] = {}
    region_v: Dict[int, NDArray[np.int64]] = {}
    region_x: Dict[int, NDArray[np.int64]] = {}
    region_xp: Dict[int, NDArray[np.int64]] = {}
    for ri in live:
        M, v = _guard_to_matrix(region_guards[ri], variables)
        region_M[ri], region_v[ri] = M, v
        region_x[ri], region_xp[ri] = _embed_x(M, n), _embed_xprime(M, n)

    solver = z3.Solver()
    w = {(q, ri): [z3.Int(f"w_{q}_{ri}_{i}") for i in range(n)] for q in states for ri in live}
    u = {(q, ri): z3.Int(f"u_{q}_{ri}") for q in states for ri in live}
    for key in w:
        for c in w[key]:
            solver.add(c >= -coeff_bound, c <= coeff_bound)
        solver.add(u[key] >= -coeff_bound, u[key] <= coeff_bound)

    # Non-negativity: region(x) ⟹ w·x + u ≥ 0
    for q in states:
        for ri in live:
            a = [-w[(q, ri)][c] for c in range(n)]
            _add_farkas_implication(solver, region_M[ri], region_v[ri], a, u[(q, ri)],
                                    lam_prefix=f"nn_{q}_{ri}", multiplier_bound=multiplier_bound)

    # Decrease: premise ⟹ V(qf,rs)(x) − V(qt,rt)(x') ≥ ζ
    aut = result.automaton_transitions
    for p_idx, prog in enumerate(prog_encs):
        A, b = prog.A, prog.b
        for rs in live:
            for rt in live:
                P0, q0 = _stack([A, region_x[rs], region_xp[rt]], [b, region_v[rs], region_v[rt]], 2 * n)
                if not _feasible(P0, q0):
                    continue
                for a_idx, edge in enumerate(aut):
                    qf, qt = edge.from_state, edge.to_state
                    zeta = 1 if edge.is_fair else 0
                    P_aut, r_aut = _guard_to_matrix(edge.guards, variables)
                    P, qv = _stack(
                        [A, _embed_x(P_aut, n), region_x[rs], region_xp[rt]],
                        [b, r_aut, region_v[rs], region_v[rt]], 2 * n,
                    )
                    a_coeffs = [-w[(qf, rs)][c] for c in range(n)] + [w[(qt, rt)][c] for c in range(n)]
                    b_rhs = u[(qf, rs)] - u[(qt, rt)] - zeta
                    _add_farkas_implication(solver, P, qv, a_coeffs, b_rhs,
                                            lam_prefix=f"dec_{p_idx}_{a_idx}_{rs}_{rt}",
                                            multiplier_bound=multiplier_bound)

    if solver.check() != z3.sat:
        return None
    model = solver.model()
    pieces: Dict[Tuple[str, int], Tuple[List[int], int]] = {}
    for q in states:
        for ri in live:
            w_vals = [model.eval(w[(q, ri)][c], model_completion=True).as_long() for c in range(n)]
            pieces[(q, ri)] = (w_vals, model.eval(u[(q, ri)], model_completion=True).as_long())
    return pieces


# --------------------------------------------------------------------------------------
# Assembly (+ merging of equal contiguous pieces for single-variable partitions)
# --------------------------------------------------------------------------------------

def _assemble(
    subset: List[str], regions: List[Region], boxes: List[Box],
    pieces: Dict[Tuple[str, int], Tuple[List[int], int]], states: List[str],
    variables: List[str], types: Dict[str, TypeDef],
) -> Dict[str, RankingFunction]:
    nonmode_typed = [v for v in variables if v not in subset and v in types]
    mode_typed = [v for v in subset if v in types]
    mode_type_bounds = {v: (types[v].min_value, types[v].max_value) for v in mode_typed}

    # Optional merge (single mode var): fuse adjacent regions with identical pieces per state.
    def region_order() -> List[int]:
        if len(subset) == 1:
            v = subset[0]
            return sorted(range(len(regions)), key=lambda ri: regions[ri][v][0])
        return list(range(len(regions)))

    rankings: Dict[str, RankingFunction] = {}
    for q in states:
        cases: List[RankingCase] = []
        order = region_order()
        merged: List[Tuple[Region, Box, Optional[Tuple[List[int], int]]]] = []
        for ri in order:
            box = boxes[ri]
            piece = pieces.get((q, ri))
            if len(subset) == 1 and merged:
                pv = subset[0]
                prev_region, prev_box, prev_piece = merged[-1]
                if (piece is not None and prev_piece is not None and piece == prev_piece
                        and box == prev_box and prev_region[pv][1] + 1 == regions[ri][pv][0]):
                    merged[-1] = ({pv: (prev_region[pv][0], regions[ri][pv][1])}, box, piece)
                    continue
            merged.append((regions[ri], box, piece))

        for region, box, piece in merged:
            mode_comps = _region_mode_comparisons(region)
            if box is None or piece is None:
                # Unreachable region: entirely inf.
                cases.append(RankingCase(mode_comps, None, True))
                continue
            w_vals, u_val = piece
            cases.append(RankingCase(_finite_guard(region, box, variables),
                                     _linear_expr(w_vals, u_val, variables), False))
            cases.extend(_box_ladder(mode_comps, nonmode_typed, box))  # inf outside reachable box
        # Mode variables out of their type range.
        cases.extend(_box_ladder([], mode_typed, mode_type_bounds))
        rankings[q] = RankingFunction(state=q, cases=cases)
    return rankings


# --------------------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------------------

def synthesize_rankings(
    result: ParseResult,
    mode_vars: Optional[List[str]] = None,
    max_regions: int = DEFAULT_MAX_REGIONS,
    max_mode_vars: int = DEFAULT_MAX_MODE_VARS,
    coeff_bound: int = DEFAULT_COEFF_BOUND,
    multiplier_bound: int = DEFAULT_MULTIPLIER_BOUND,
    reach_budget: int = DEFAULT_REACH_BUDGET,
) -> Dict[str, RankingFunction]:
    """Synthesize a (piecewise) linear ranking per automaton state, coarsest-first (fewest cases).

    `mode_vars`, if given, forces the partition variables (skipping auto-detection). Raises
    SynthesisError if nothing in the search space works.
    """
    if not result.automaton_transitions:
        raise SynthesisError(
            "No automaton transitions: provide `trans(...)` or an LTL `spec:` before synthesizing."
        )
    variables = _collect_variables(result)
    if not variables:
        raise SynthesisError("No program variables found.")
    states = _automaton_states(result)
    types = result.types

    prog_encs = [
        encode_transition(cmd, variables, nonstrict_only=True, types=types)
        for cmd in (result.commands or [])
    ]
    reachable = _reachable_states(result, variables, types, reach_budget)

    splits = _guard_split_points(result)
    var_intervals: Dict[str, List[Tuple[int, int]]] = {
        v: _intervals(splits.get(v, set()), types[v]) for v in variables if v in types
    }

    if mode_vars is not None:
        missing = [v for v in mode_vars if v not in var_intervals]
        if missing:
            raise SynthesisError(f"--mode variables must be type-declared: {', '.join(missing)}")
        candidate_subsets = [mode_vars]
    else:
        candidate_subsets = _mode_partitions(variables, var_intervals, max_regions, max_mode_vars)

    for subset in candidate_subsets:
        regions = _regions_of(subset, var_intervals)
        boxes = [_region_box(r, subset, variables, types, reachable) for r in regions]
        if all(b is None for b in boxes):
            continue  # no reachable states in any region (shouldn't happen for a real program)
        region_guards: List[Optional[List[Comparison]]] = [
            _finite_guard(regions[ri], boxes[ri], variables) if boxes[ri] is not None else None
            for ri in range(len(regions))
        ]
        pieces = _solve_partition(result, variables, states, prog_encs, regions, boxes,
                                  region_guards, coeff_bound, multiplier_bound)
        if pieces is not None:
            return _assemble(subset, regions, boxes, pieces, states, variables, types)

    raise SynthesisError(
        "No piecewise linear ranking found in the search space (max_mode_vars="
        f"{max_mode_vars}, max_regions={max_regions}). The program may need a lexicographic ranking "
        "or richer invariants (Tier 3), or type-bounded variables are missing."
    )


def synthesize_into(result: ParseResult, **kwargs) -> ParseResult:
    """Synthesize rankings for states that don't already have one; fill them into `result`."""
    synthesized = synthesize_rankings(result, **kwargs)
    for state, rf in synthesized.items():
        result.ranking_functions.setdefault(state, rf)
    return result
