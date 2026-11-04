"""Guess-and-check (Houdini) inference of per-automaton-state inductive invariants.

For every automaton state q we look for a conjunction I_q of linear atoms such that

- every initial product state (q0, x0) satisfies I_q0, and
- every product step (q, x) -> (q', x') with I_q(x) lands in I_q'(x').

Candidates ("guesses") are:

- place-invariant equalities  y·x == y·x0  for a basis of {y : y·delta_c = 0 for every command c}
  (only when every command's update is x' = x + const, which holds for P/T nets);
- non-negativity  x >= 0;
- every atom of the program guards, the automaton guards and the APs, together with its negation.

Houdini starts from all candidates and repeatedly drops those that some step can violate
(counterexample-guided: one solver model may knock out several candidates at once), until the
remainder is inductive. Steps are checked over the *reals* using the same integer-tightened
matrices as the Farkas-based verifier, so every invariant found here is also provable there.
"""

from __future__ import annotations

import itertools
import time
from fractions import Fraction
from typing import Dict, List, Optional

import numpy as np
import z3

from zkterm_tool.ast_types import BinOp, Comparison, CompOp, Num, Var
from zkterm_tool.encoder import encode_transition, expr_to_linear
from zkterm_tool.ltl import negate_comparison
from zkterm_tool.parser import ParseResult
from zkterm_tool.synth import _collect_variables, _guard_to_matrix


def _key(c: Comparison) -> tuple:
    M, v = _guard_to_matrix([c], _key.variables)  # type: ignore[attr-defined]
    return tuple(map(tuple, M.tolist())), tuple(v.tolist())


def _linear_comparison(coeffs: Dict[str, int], const: int, op: CompOp) -> Comparison:
    """Build `sum(coeffs * x) op const` as a Comparison."""
    expr = None
    for var, c in coeffs.items():
        if c == 0:
            continue
        term = Var(var) if c == 1 else BinOp("*", Num(c), Var(var))
        expr = term if expr is None else BinOp("+", expr, term)
    return Comparison(left=expr if expr is not None else Num(0), right=Num(const), op=op)


def place_invariants(result: ParseResult, variables: List[str],
                     commands: Optional[List[int]] = None) -> List[Comparison]:
    """Integer basis of linear equalities preserved by every command whose update is x + const
    (restricted to `commands` if given)."""
    n = len(variables)
    idx = {v: i for i, v in enumerate(variables)}
    deltas = []
    for ci, cmd in enumerate(result.commands):
        if commands is not None and ci not in commands:
            continue
        d = [0] * n
        for a in cmd.assignments:
            le = expr_to_linear(a.expr)
            if dict(le.coeffs) != {a.var: 1}:
                return []  # not a constant-delta update: give up on P-invariants
            d[idx[a.var]] = int(le.const)
        deltas.append(d)
    if not deltas:
        return []
    # Left null space of the incidence matrix: y with sum_i y_i * d_c[i] = 0 for all c.
    basis = _nullspace([[Fraction(d[i]) for d in deltas] for i in range(n)])
    x0 = _initial_point(result, variables)
    out = []
    for y in basis:
        den = 1
        for f in y:
            den = den * f.denominator // np.gcd(den, f.denominator)
        yi = [int(f * den) for f in y]
        g = 0
        for c in yi:
            g = int(np.gcd(g, abs(c)))
        yi = [c // g for c in yi] if g else yi
        coeffs = {variables[i]: yi[i] for i in range(n) if yi[i]}
        if x0 is None:
            continue
        const = sum(yi[i] * x0[i] for i in range(n))
        out.append(_linear_comparison(coeffs, const, CompOp.EQ))
    return out


def subinvariants(result: ParseResult, variables: List[str], max_lps: int = 200,
                  commands: Optional[List[int]] = None) -> List[Comparison]:
    """Structural (state-equation) bounds as inductive inequalities  y·x <= y·x0.

    For each target expression e (every variable, and both directions of every guard/AP
    expression) solve  min y·x0  s.t.  y·delta_c <= 0 for every command c,  y >= e  (componentwise,
    valid because all variables are non-negative token counts). No command increases y·x, so the
    resulting inequality holds on every reachable state and is inductive on its own. Requires
    constant-delta updates (P/T nets); returns [] otherwise. `commands` restricts the net to a
    subset of commands (a guess that the others are dead; Houdini re-checks it)."""
    n = len(variables)
    idx = {v: i for i, v in enumerate(variables)}
    deltas = []
    for ci, cmd in enumerate(result.commands):
        if commands is not None and ci not in commands:
            continue
        d = [0] * n
        for a in cmd.assignments:
            le = expr_to_linear(a.expr)
            if dict(le.coeffs) != {a.var: 1}:
                return []
            d[idx[a.var]] = int(le.const)
        deltas.append(d)
    x0 = _initial_point(result, variables)
    if x0 is None or not deltas:
        return []

    targets: List[List[int]] = [[1 if j == i else 0 for j in range(n)] for i in range(n)]
    guards = [g for cmd in result.commands for g in cmd.guards]
    guards += [g for t in result.automaton_transitions for g in t.guards]
    guards += [g for conj in result.aps.values() for g in conj]
    for g in guards:
        le_l, le_r = expr_to_linear(g.left), expr_to_linear(g.right)
        e = [0] * n
        for v, c in le_l.coeffs.items():
            e[idx[v]] += int(c)
        for v, c in le_r.coeffs.items():
            e[idx[v]] -= int(c)
        if any(e):
            targets.append(e)
            targets.append([-c for c in e])
    seen, uniq = set(), []
    for t in targets:
        if tuple(t) not in seen:
            seen.add(tuple(t))
            uniq.append(t)

    out: List[Comparison] = []
    for e in uniq[:max_lps]:
        opt = z3.Optimize()
        y = [z3.Real(f"y{i}") for i in range(n)]
        for i in range(n):
            opt.add(y[i] >= e[i])
        for d in deltas:
            opt.add(z3.Sum([d[i] * y[i] for i in range(n) if d[i]] or [z3.RealVal(0)]) <= 0)
        obj = z3.Sum([x0[i] * y[i] for i in range(n) if x0[i]] or [z3.RealVal(0)])
        h = opt.minimize(obj)
        if opt.check() != z3.sat:
            continue
        m = opt.model()
        if not z3.is_rational_value(opt.lower(h)):
            continue  # unbounded: no finite structural bound for this direction
        ys = [Fraction(m.eval(y[i], model_completion=True).as_fraction()) for i in range(n)]
        den = 1
        for f in ys:
            den = den * f.denominator // int(np.gcd(den, f.denominator))
        yi = [int(f * den) for f in ys]
        if not any(yi):
            continue
        coeffs = {variables[i]: yi[i] for i in range(n) if yi[i]}
        out.append(_linear_comparison(coeffs, sum(yi[i] * x0[i] for i in range(n)), CompOp.LE))
    return out


def _nullspace(rows: List[List[Fraction]]) -> List[List[Fraction]]:
    """Basis of {y : y · column_c = 0 for all c}, where rows[i][c] is entry (i, c)."""
    n = len(rows)
    m = len(rows[0]) if rows else 0
    # Solve D^T y = 0: matrix with m rows (one per command) and n columns.
    mat = [[rows[i][c] for i in range(n)] for c in range(m)]
    pivots = []
    r = 0
    for col in range(n):
        piv = next((i for i in range(r, m) if mat[i][col] != 0), None)
        if piv is None:
            continue
        mat[r], mat[piv] = mat[piv], mat[r]
        pv = mat[r][col]
        mat[r] = [x / pv for x in mat[r]]
        for i in range(m):
            if i != r and mat[i][col] != 0:
                f = mat[i][col]
                mat[i] = [a - f * b for a, b in zip(mat[i], mat[r])]
        pivots.append(col)
        r += 1
        if r == m:
            break
    free = [c for c in range(n) if c not in pivots]
    basis = []
    for fcol in free:
        y = [Fraction(0)] * n
        y[fcol] = Fraction(1)
        for i, pcol in enumerate(pivots):
            y[pcol] = -mat[i][fcol]
        basis.append(y)
    return basis


def _initial_point(result: ParseResult, variables: List[str]) -> Optional[List[int]]:
    vals = {}
    for c in result.init_condition or []:
        if c.op == CompOp.EQ and isinstance(c.left, Var) and isinstance(c.right, Num):
            vals[c.left.name] = c.right.value
    if set(vals) != set(variables):
        return None
    return [vals[v] for v in variables]


def candidate_atoms(result: ParseResult, variables: List[str], bound: Optional[int]) -> List[Comparison]:
    atoms: List[Comparison] = []
    atoms.extend(place_invariants(result, variables))
    atoms.extend(subinvariants(result, variables))
    for v in variables:
        atoms.append(Comparison(Var(v), Num(0), CompOp.GE))
        if bound is not None:
            atoms.append(Comparison(Var(v), Num(bound), CompOp.LE))
    guards = [g for cmd in result.commands for g in cmd.guards]
    guards += [g for t in result.automaton_transitions for g in t.guards]
    guards += [g for conj in result.aps.values() for g in conj]
    for g in guards:
        atoms.append(g)
        atoms.extend(negate_comparison(g))
    # Deduplicate by matrix encoding.
    _key.variables = variables  # type: ignore[attr-defined]
    seen, out = set(), []
    for a in atoms:
        k = _key(a)
        if k not in seen:
            seen.add(k)
            out.append(a)
    return out


FALSE = Comparison(Num(0), Num(1), CompOp.GE)


def _hull_2d(points) -> List[tuple]:
    """Facets (a, b, c) meaning a*x + b*y <= c of the convex hull of 2D integer points."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]  # counter-clockwise
    out = []
    for u, v in zip(hull, hull[1:] + hull[:1]):
        a, b = v[1] - u[1], u[0] - v[0]  # outward normal of the edge u -> v
        g = int(np.gcd(abs(a), abs(b))) or 1
        a, b = a // g, b // g
        out.append((a, b, a * u[0] + b * u[1]))
    return out


def relational_candidates(variables: List[str], marks, max_pairs_vars: int = 40,
                          groups: Optional[List[List[str]]] = None) -> List[Comparison]:
    """Relational guesses from sampled markings: the equalities of their affine hull, and for
    every pair of non-constant variables the facets of the convex hull of their joint values
    (which includes the octagon bounds on x + y and x - y). `groups` (e.g. the slots of one
    array-valued variable) add their sums as terms of such pairs."""
    import random

    X = np.array(sorted(marks), dtype=np.int64)
    var = [i for i in range(len(variables)) if X[:, i].min() < X[:, i].max()]
    out: List[Comparison] = []
    if not var:
        return out
    # Affine hull, from a sample of the points (Houdini's sample filter drops wrong equalities).
    pts = [list(map(int, x)) for x in X]
    if len(pts) > 400:
        pts = random.Random(0).sample(pts, 400)
    rows = [[Fraction(p[i]) for p in pts] for i in var] + [[Fraction(1)] * len(pts)]
    for y in _nullspace(rows):
        den = 1
        for f in y:
            den = den * f.denominator // int(np.gcd(den, f.denominator))
        yi = [int(f * den) for f in y]
        if not any(yi[:-1]) or max(abs(c) for c in yi) > 1000:
            continue
        coeffs = {variables[var[j]]: yi[j] for j in range(len(var)) if yi[j]}
        out.append(_linear_comparison(coeffs, -yi[-1], CompOp.EQ))
    # Terms: single variables and group sums, as (coefficients by variable, column of values).
    terms = [({variables[i]: 1}, X[:, i]) for i in var]
    idx = {v: i for i, v in enumerate(variables)}
    for g in groups or []:
        cols = [idx[v] for v in g if v in idx]
        if len(cols) >= 2:
            col = X[:, cols].sum(axis=1)
            if col.min() < col.max():
                terms.append(({variables[i]: 1 for i in cols}, col))
    if len(terms) <= max_pairs_vars + 2 * len(groups or []):
        for (ta, xa), (tb, xb) in itertools.combinations(terms, 2):
            for ca, cb, c in _hull_2d(zip(xa.tolist(), xb.tolist())):
                if not (ca and cb):
                    continue  # axis-parallel facets are bounds of a single term
                coeffs: Dict[str, int] = {}
                for v, k in ta.items():
                    coeffs[v] = coeffs.get(v, 0) + ca * k
                for v, k in tb.items():
                    coeffs[v] = coeffs.get(v, 0) + cb * k
                coeffs = {v: k for v, k in coeffs.items() if k}
                if coeffs:
                    out.append(_linear_comparison(coeffs, c, CompOp.LE))
    return out


def sampled_candidates(result: ParseResult, budget: int, samples=None,
                       relational: bool = False, groups=None) -> Dict[str, List[Comparison]]:
    """Guesses from explicitly explored product states (up to `budget` of them).

    Per automaton state q: the bounds of every variable over the markings seen in q, and `false`
    for automaton states never reached; with `relational`, also `relational_candidates`. These
    are only guesses: Houdini keeps the inductive ones, so a truncated exploration (too-tight
    bounds, states wrongly guessed unreachable) stays sound."""
    from explicit_check import sample_markings

    variables, per_state, fired, _complete = samples or sample_markings(result, budget)
    states = ({t.from_state for t in result.automaton_transitions}
              | {t.to_state for t in result.automaton_transitions}
              | set(result.automaton_initial_states or []))
    out: Dict[str, List[Comparison]] = {}
    for q in states:
        marks = per_state.get(q)
        if not marks:
            out[q] = [FALSE]
            continue
        atoms = []
        for i, v in enumerate(variables):
            lo = min(m[i] for m in marks)
            hi = max(m[i] for m in marks)
            if lo == hi:
                atoms.append(Comparison(Var(v), Num(lo), CompOp.EQ))
            else:
                atoms.append(Comparison(Var(v), Num(lo), CompOp.GE))
                atoms.append(Comparison(Var(v), Num(hi), CompOp.LE))
        if relational:
            atoms += relational_candidates(variables, marks, groups=groups)
        out[q] = atoms
    # Commands that never fired in the samples are guessed dead: invariants of the remaining
    # subnet become candidates everywhere (they may only hold because those commands stay dead).
    if len(fired) < len(result.commands):
        live = sorted(fired)
        extra = place_invariants(result, variables, live) + subinvariants(result, variables, commands=live)
        for q in out:
            if out[q] != [FALSE]:
                out[q] = out[q] + extra
    return out


class _Rows:
    """A comparison as matrix rows M x <= v (integer-tightened), for fast evaluation and z3."""

    def __init__(self, c: Comparison, variables: List[str]):
        self.c = c
        M, v = _guard_to_matrix([c], variables)
        self.M = M
        self.v = v

    def holds(self, x: List[Fraction]) -> bool:
        for row, rhs in zip(self.M.tolist(), self.v.tolist()):
            if sum(Fraction(a) * xi for a, xi in zip(row, x) if a) > rhs:
                return False
        return True

    def z3(self, X) -> list:
        return [z3.Sum([int(a) * X[i] for i, a in enumerate(row) if a] or [z3.RealVal(0)]) <= int(rhs)
                for row, rhs in zip(self.M.tolist(), self.v.tolist())]


def houdini(result: ParseResult, bound: Optional[int] = None, time_budget: float = 60.0,
            prune: bool = True,
            candidates: Optional[Dict[str, List[Comparison]]] = None,
            sample_budget: int = 0, relational: bool = False,
            groups: Optional[List[List[str]]] = None) -> Dict[str, List[Comparison]]:
    """Largest inductive subset of the candidate atoms, per automaton state.

    `candidates` overrides the default guesses with a fixed pool per state; the result equals the
    pool exactly when the pool is already inductive. `sample_budget` > 0 adds guesses from that
    many explicitly explored product states (see sampled_candidates, also for `relational`)."""
    deadline = time.monotonic() + time_budget
    variables = _collect_variables(result)
    n = len(variables)
    states = sorted({t.from_state for t in result.automaton_transitions}
                    | {t.to_state for t in result.automaton_transitions}
                    | set(result.automaton_initial_states or []))
    if candidates is None:
        pool = [_Rows(c, variables) for c in candidate_atoms(result, variables, bound)]
        cands: Dict[str, List[_Rows]] = {q: list(pool) for q in states}
        if sample_budget > 0:
            from explicit_check import sample_markings
            samples = sample_markings(result, sample_budget)
            for q, atoms in sampled_candidates(result, sample_budget, samples, relational, groups).items():
                cands[q] = cands[q] + [_Rows(c, variables) for c in atoms]
            # Sampled product states are reachable: a guess false at one of them cannot be an
            # invariant of its automaton state, so drop it before any solver call (Daikon-style).
            svars, per_state, _fired, _complete = samples
            perm = [svars.index(v) for v in variables]
            for q in states:
                marks = per_state.get(q)
                if not marks:
                    continue
                X_s = np.array([[m[j] for j in perm] for m in marks], dtype=np.int64)
                cands[q] = [r for r in cands[q]
                            if r.M.shape[0] == 0 or bool(((X_s @ r.M.T) <= r.v).all())]
    else:
        cands = {q: [_Rows(c, variables) for c in candidates.get(q, [])] for q in states}

    X = [z3.Real(f"x{i}") for i in range(n)]
    XP = [z3.Real(f"xp{i}") for i in range(n)]

    # Initial states: keep only atoms implied by the init condition.
    init_rows = [_Rows(c, variables) for c in (result.init_condition or [])]
    for q in result.automaton_initial_states or []:
        s = z3.Solver()
        for r in init_rows:
            s.add(*r.z3(X))
        if s.check() == z3.unsat:
            continue
        kept = []
        for r in cands[q]:
            s.push()
            s.add(z3.Not(z3.And(r.z3(X))))
            if s.check() == z3.unsat:
                kept.append(r)
            s.pop()
        cands[q] = kept

    progs = []
    for cmd in result.commands:
        enc = encode_transition(cmd, variables, nonstrict_only=True, types=result.types)
        progs.append((enc.A.tolist(), enc.b.tolist()))
    edges = []
    for t in result.automaton_transitions:
        M, v = _guard_to_matrix(t.guards, variables)
        edges.append((t.from_state, t.to_state, M.tolist(), v.tolist()))

    def premise(solver, q, A, b, M, v):
        for r in cands[q]:
            solver.add(*r.z3(X))
        for row, rhs in zip(M, v):
            solver.add(z3.Sum([int(a) * X[i] for i, a in enumerate(row) if a] or [z3.RealVal(0)]) <= int(rhs))
        XX = X + XP
        for row, rhs in zip(A, b):
            solver.add(z3.Sum([int(a) * XX[i] for i, a in enumerate(row) if a] or [z3.RealVal(0)]) <= int(rhs))

    work = [(qf, qt, pi, ei) for ei, (qf, qt, _, _) in enumerate(edges) for pi in range(len(progs))]
    dirty = set(work)
    while dirty:
        if time.monotonic() > deadline:
            raise TimeoutError("houdini budget exceeded")
        item = next(iter(dirty))
        dirty.discard(item)
        qf, qt, pi, ei = item
        A, b = progs[pi]
        _, _, M, v = edges[ei]
        s = z3.Solver()
        premise(s, qf, A, b, M, v)
        if s.check() == z3.unsat:
            continue
        changed = False
        while cands[qt]:
            s.push()
            s.add(z3.Or([z3.Not(z3.And(r.z3(XP))) for r in cands[qt]]))
            res = s.check()
            if res != z3.sat:
                s.pop()
                break
            m = s.model()
            xp = [_val(m, XP[i]) for i in range(n)]
            s.pop()
            before = len(cands[qt])
            cands[qt] = [r for r in cands[qt] if r.holds(xp)]
            changed = changed or len(cands[qt]) != before
        if changed:
            # I_qt got weaker: every step leaving qt must be rechecked (and qt == qf restarts too).
            for w in work:
                if w[0] == qt:
                    dirty.add(w)
    out = {q: [r.c for r in cands[q]] for q in states}
    if prune:
        out = {q: _prune(cs, variables) for q, cs in out.items()}
    return out


def _val(m, x) -> Fraction:
    v = m.eval(x, model_completion=True)
    if z3.is_rational_value(v):
        return Fraction(v.numerator_as_long(), v.denominator_as_long())
    return Fraction(str(v.as_decimal(20)).rstrip("?"))


def _prune(atoms: List[Comparison], variables: List[str]) -> List[Comparison]:
    """Drop atoms implied (over the reals) by the remaining ones; keeps the conjunction equivalent."""
    n = len(variables)
    X = [z3.Real(f"x{i}") for i in range(n)]
    rows = [_Rows(c, variables) for c in atoms]
    s = z3.Solver()
    for r in rows:
        s.add(*r.z3(X))
    if s.check() == z3.unsat:
        return [Comparison(Num(0), Num(1), CompOp.GE)]  # empty region: the state is unreachable
    kept = list(rows)
    # Try to drop non-equalities first (place invariants are the most useful to keep).
    order = sorted(range(len(kept)), key=lambda i: kept[i].c.op == CompOp.EQ)
    alive = [True] * len(kept)
    for i in order:
        s = z3.Solver()
        for j, r in enumerate(kept):
            if j != i and alive[j]:
                s.add(*r.z3(X))
        s.add(z3.Not(z3.And(kept[i].z3(X))))
        if s.check() == z3.unsat:
            alive[i] = False
    return [r.c for j, r in enumerate(kept) if alive[j]]


def needed_atoms(result: ParseResult, invariants: Dict[str, List[Comparison]],
                 rankings, time_budget: float = 120.0) -> Dict[str, List[Comparison]]:
    """Smallest-effort subset of `invariants` that still supports `rankings` and stays inductive.

    1. Seed: for every obligation the ranking needs (non-negativity of each finite case, and
       decrease along every program step × automaton edge between finite cases), the unsat core of
       its negation over the invariant atoms (as assumptions) marks the atoms the proof uses.
    2. Close under inductiveness: every kept atom of q' must follow, after one step, from kept
       atoms of each predecessor q; the unsat cores of those checks add support atoms. States whose
       invariant is `false` keep `false`, and their predecessors keep what makes them unreachable.

    Every check runs over the reals with integer-tightened rows, like the Farkas verifier."""
    deadline = time.monotonic() + time_budget
    variables = _collect_variables(result)
    n = len(variables)
    X = [z3.Real(f"x{i}") for i in range(n)]
    XP = [z3.Real(f"xp{i}") for i in range(n)]
    rows = {q: [_Rows(c, variables) for c in cs] for q, cs in invariants.items()}

    def lin(expr, V):
        le = expr_to_linear(expr)
        idx = {v: i for i, v in enumerate(variables)}
        return z3.Sum([int(c) * V[idx[v]] for v, c in le.coeffs.items() if c] or [z3.RealVal(0)]) + int(le.const)

    def rows_z3(M, v, V):
        return [z3.Sum([int(a) * V[i] for i, a in enumerate(row) if a] or [z3.RealVal(0)]) <= int(rhs)
                for row, rhs in zip(M, v)]

    progs = []
    for cmd in result.commands:
        enc = encode_transition(cmd, variables, nonstrict_only=True, types=result.types)
        progs.append(rows_z3(enc.A.tolist(), enc.b.tolist(), X + XP))
    edges = []
    for t in result.automaton_transitions:
        M, v = _guard_to_matrix(t.guards, variables)
        edges.append((t.from_state, t.to_state, t.is_fair, rows_z3(M.tolist(), v.tolist(), X)))

    # Finite cases per state: (region comparisons without invariant atoms, linear expression).
    finite: Dict[str, list] = {}
    for q, rf in rankings.items():
        inv_ids = {id(a) for a in invariants.get(q, [])}
        for case in rf.cases:
            if case.is_infinity:
                continue
            region = [g for g in case.guards if id(g) not in inv_ids]
            M, v = _guard_to_matrix(region, variables)
            finite.setdefault(q, []).append((M.tolist(), v.tolist(), case.expression))

    needed: Dict[str, set] = {q: set() for q in invariants}
    for q, cs in invariants.items():
        if len(cs) == 1 and cs[0].left == FALSE.left and cs[0].right == FALSE.right and cs[0].op == FALSE.op:
            needed[q].add(0)

    def core(solver_facts, assumptions_by_state) -> Dict[str, set]:
        """Unsat core of facts + tagged invariant atoms; maps state -> atom indices used."""
        s = z3.Solver()
        s.set("core.minimize", True)
        s.add(*solver_facts)
        tags = {}
        for (q, side), idxs in assumptions_by_state.items():
            V = XP if side == "xp" else X
            for i in idxs:
                b = z3.Bool(f"a_{q}_{side}_{i}")
                s.add(z3.Implies(b, z3.And(rows[q][i].z3(V))))
                tags[b] = (q, i)
        res = s.check(*tags.keys())
        if res != z3.unsat:
            raise RuntimeError("expected an unsat obligation")
        out: Dict[str, set] = {}
        for b in s.unsat_core():
            q, i = tags[b]
            out.setdefault(q, set()).add(i)
        return out

    def add(found):
        new = []
        for q, idxs in found.items():
            for i in idxs - needed[q]:
                needed[q].add(i)
                new.append((q, i))
        return new

    work = []
    # 1a. Non-negativity of every finite case.
    for q, cases in finite.items():
        for M, v, expr in cases:
            facts = rows_z3(M, v, X) + [lin(expr, X) < 0]
            work += add(core(facts, {(q, "x"): range(len(rows[q]))}))
    # 1b. Decrease along every step between finite cases (only where the premise is satisfiable
    #     with the full invariants does the obligation constrain anything).
    for qf, qt, fair, guard in edges:
        if qf not in finite or qt not in finite:
            continue
        zeta = 1 if fair else 0
        for prog in progs:
            for Ms, vs, es in finite[qf]:
                for Mt, vt, et in finite[qt]:
                    if time.monotonic() > deadline:
                        raise TimeoutError("needed_atoms budget exceeded")
                    facts = (prog + guard + rows_z3(Ms, vs, X) + rows_z3(Mt, vt, XP)
                             + [lin(es, X) - lin(et, XP) < zeta])
                    work += add(core(facts, {(qf, "x"): range(len(rows[qf])),
                                             (qt, "xp"): range(len(rows[qt]))}))
    for q in needed:
        work += [(q, i) for i in needed[q]]

    # 2. Close under inductiveness.
    incoming: Dict[str, list] = {}
    for qf, qt, fair, guard in edges:
        incoming.setdefault(qt, []).append((qf, guard))
    seen_work = set()
    while work:
        if time.monotonic() > deadline:
            raise TimeoutError("needed_atoms budget exceeded")
        qt, i = work.pop()
        if (qt, i) in seen_work:
            continue
        seen_work.add((qt, i))
        atom_xp = z3.And(rows[qt][i].z3(XP))
        for qf, guard in incoming.get(qt, []):
            for prog in progs:
                facts = prog + guard + [z3.Not(atom_xp)]
                work += add(core(facts, {(qf, "x"): range(len(rows[qf]))}))
        # Initial states must satisfy the kept atoms; they satisfy all of `invariants` already.

    return {q: [invariants[q][i] for i in sorted(needed[q])] for q in invariants}
