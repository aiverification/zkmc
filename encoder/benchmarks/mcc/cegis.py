"""Guess-and-check (CEGIS) synthesis of one linear ranking piece per automaton state.

The Farkas-template LP in zkterm_tool.synth encodes every obligation at once, which blows up for
large automata (|commands| x |automaton edges| implications, each with its own multipliers).
Here the ranking is guessed from data and checked symbolically instead:

1. Guess: solve a small LP over the unknown coefficients (w_q, u_q) only, constrained by
   decrease along sampled product edges and non-negativity at sampled product states.
2. Check: for every (command, automaton edge q -> q') look for a step inside the invariants
   I_q / I_q' that violates the decrease, and for every q a point of I_q where V_q < 0
   (over the reals, like the Farkas verifier).
3. Every counterexample becomes one more LP constraint; repeat until none is left.

Finally the rational solution is scaled to integers. The verifier re-checks everything.
"""

from __future__ import annotations

import time
from fractions import Fraction
from math import lcm
from typing import Dict, List, Optional

import z3

from zkterm_tool.ast_types import Comparison
from zkterm_tool.encoder import encode_transition
from zkterm_tool.parser import ParseResult
from zkterm_tool.ranking_types import RankingFunction
from zkterm_tool.synth import _assemble_invariant, _collect_variables, _guard_to_matrix


class CegisFailure(Exception):
    pass


def _rows(M, v, V):
    return [z3.Sum([int(a) * V[i] for i, a in enumerate(row) if a] or [z3.RealVal(0)]) <= int(rhs)
            for row, rhs in zip(M, v)]


def _num(v) -> Fraction:
    return Fraction(v.as_fraction()) if z3.is_rational_value(v) else Fraction(str(v.as_decimal(30)).rstrip("?"))


def sample_edges(result: ParseResult, budget: int):
    """Breadth-first product exploration; returns (variables, states, edges) with
    edges = [(q, x, q', x', fair)] and states = {(q, x)}."""
    from collections import deque

    from explicit_check import _holds, _product

    variables, x0, program_successors, by_state = _product(result)
    seen, edges = set(), []
    queue = deque()
    for q in result.automaton_initial_states or []:
        seen.add((q, x0))
        queue.append((q, x0))
    while queue and len(seen) < budget:
        q, x = queue.popleft()
        enabled = [(to, fair) for guard, to, fair in by_state.get(q, []) if _holds(guard, x)]
        for y in program_successors(x):
            for to, fair in enabled:
                edges.append((q, x, to, y, fair))
                if (to, y) not in seen and len(seen) < budget:
                    seen.add((to, y))
                    queue.append((to, y))
    return variables, seen, edges


def cegis_rankings(result: ParseResult, invariants: Dict[str, List[Comparison]],
                   sample_budget: int = 20_000, time_budget: float = 300.0,
                   max_rounds: int = 200) -> Dict[str, RankingFunction]:
    deadline = time.monotonic() + time_budget
    variables = _collect_variables(result)
    n = len(variables)
    X = [z3.Real(f"x{i}") for i in range(n)]
    XP = [z3.Real(f"xp{i}") for i in range(n)]

    def inv_rows(q, V):
        M, v = _guard_to_matrix(invariants[q], variables)
        return _rows(M.tolist(), v.tolist(), V)

    states = sorted(invariants)
    live = []
    for q in states:
        s = z3.Solver()
        s.add(*inv_rows(q, X))
        if s.check() != z3.unsat:
            live.append(q)

    # Unknowns of the guess LP.
    W = {q: [z3.Real(f"w_{q}_{i}") for i in range(n)] for q in live}
    U = {q: z3.Real(f"u_{q}") for q in live}

    def V(q, point):  # linear in the unknowns, point is numeric
        return z3.Sum([W[q][i] * point[i] for i in range(n) if point[i]] or [z3.RealVal(0)]) + U[q]

    lp = z3.Solver()
    svars, sstates, sedges = sample_edges(result, sample_budget)
    perm = [svars.index(v) for v in variables]
    for q, x in sstates:
        if q in W:
            lp.add(V(q, [x[j] for j in perm]) >= 0)
    for q, x, q2, y, fair in sedges:
        if q in W and q2 in W:
            lp.add(V(q, [x[j] for j in perm]) - V(q2, [y[j] for j in perm]) >= (1 if fair else 0))

    # Checks: one incremental solver per (command, automaton edge) between live states, and one
    # per live state for non-negativity. Premises never change, only the candidate ranking does.
    checks = []
    for cmd in result.commands:
        enc = encode_transition(cmd, variables, nonstrict_only=True, types=result.types)
        step = _rows(enc.A.tolist(), enc.b.tolist(), X + XP)
        for t in result.automaton_transitions:
            if t.from_state not in W or t.to_state not in W:
                continue
            M, v = _guard_to_matrix(t.guards, variables)
            s = z3.Solver()
            s.add(*step, *_rows(M.tolist(), v.tolist(), X), *inv_rows(t.from_state, X),
                  *inv_rows(t.to_state, XP))
            if s.check() == z3.unsat:
                continue  # vacuous step
            checks.append(("dec", s, t.from_state, t.to_state, 1 if t.is_fair else 0))
    for q in live:
        s = z3.Solver()
        s.add(*inv_rows(q, X))
        checks.append(("nn", s, q, None, 0))

    for _round in range(max_rounds):
        if time.monotonic() > deadline:
            raise CegisFailure("time budget exceeded")
        lp.set("timeout", max(1, int((deadline - time.monotonic()) * 1000)))
        if lp.check() != z3.sat:
            raise CegisFailure("no linear ranking per automaton state fits the samples/counterexamples")
        m = lp.model()
        w = {q: [_num(m.eval(c, model_completion=True)) for c in W[q]] for q in live}
        u = {q: _num(m.eval(U[q], model_completion=True)) for q in live}

        def cand(q, VV):
            return z3.Sum([z3.RealVal(w[q][i]) * VV[i] for i in range(n) if w[q][i]] or [z3.RealVal(0)]) \
                + z3.RealVal(u[q])

        found = 0
        for kind, s, q, q2, zeta in checks:
            if time.monotonic() > deadline:
                raise CegisFailure("time budget exceeded")
            s.push()
            if kind == "dec":
                s.add(cand(q, X) - cand(q2, XP) < zeta)
            else:
                s.add(cand(q, X) < 0)
            if s.check() == z3.sat:
                cm = s.model()
                x = [_num(cm.eval(X[i], model_completion=True)) for i in range(n)]
                if kind == "dec":
                    y = [_num(cm.eval(XP[i], model_completion=True)) for i in range(n)]
                    lp.add(V(q, x) - V(q2, y) >= zeta)
                else:
                    lp.add(V(q, x) >= 0)
                found += 1
            s.pop()
        if found == 0:
            scale = 1
            for q in live:
                for f in w[q] + [u[q]]:
                    scale = lcm(scale, f.denominator)
            pieces = {(q, 0): ([int(f * scale) for f in w[q]], int(u[q] * scale)) for q in live}
            return _assemble_invariant([], [{}], pieces, states, variables, invariants, {})
    raise CegisFailure("too many refinement rounds")
