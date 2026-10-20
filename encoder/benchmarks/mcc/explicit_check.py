"""Independent explicit-state check of a `.gc` program against its Büchi automaton.

Explores the synchronous product of the program and the automaton exactly as the verifier
defines it (a product step (q, x) -> (q', x') takes a program command enabled at x and an
automaton transition q -> q' whose guard holds at x; it is fair iff the automaton transition
is fair). The property holds iff no fair edge lies on a reachable cycle.

When it holds, the exact ranking is returned as well: for every reachable product state, the
maximum number of fair edges on any path from it. It is constant on each strongly connected
component, non-increasing along every edge and strictly decreasing along fair edges, so it is
a valid ranking on the reachable states (all other states can be `inf`).

This deliberately re-implements expression evaluation rather than reusing the encoder's, so
that agreement with MCC verdicts checks the translation and the product semantics end to end.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from zkterm_tool.ast_types import BinOp, Comparison, CompOp, Expr, Neg, Num, Var
from zkterm_tool.parser import ParseResult


class TooLarge(Exception):
    """The product exceeds the state budget."""


Linear = tuple[dict[int, int], int]  # (coefficients by variable index, constant)


def _linear(e: Expr, idx: dict[str, int]) -> Linear:
    if isinstance(e, Num):
        return {}, e.value
    if isinstance(e, Var):
        return {idx[e.name]: 1}, 0
    if isinstance(e, Neg):
        c, k = _linear(e.expr, idx)
        return {i: -v for i, v in c.items()}, -k
    if isinstance(e, BinOp):
        lc, lk = _linear(e.left, idx)
        rc, rk = _linear(e.right, idx)
        if e.op in ("+", "-"):
            s = 1 if e.op == "+" else -1
            out = dict(lc)
            for i, v in rc.items():
                out[i] = out.get(i, 0) + s * v
            return out, lk + s * rk
        if e.op == "*":
            if not lc:
                return {i: lk * v for i, v in rc.items()}, lk * rk
            if not rc:
                return {i: rk * v for i, v in lc.items()}, lk * rk
            raise ValueError("non-linear multiplication")
    raise ValueError(f"unsupported expression {e!r}")


def _compile_comparison(c: Comparison, idx: dict[str, int]):
    """Return (coeffs, const, op) for `sum(coeffs * x) + const  op  0`."""
    lc, lk = _linear(c.left, idx)
    rc, rk = _linear(c.right, idx)
    coeffs = dict(lc)
    for i, v in rc.items():
        coeffs[i] = coeffs.get(i, 0) - v
    return tuple((i, v) for i, v in coeffs.items() if v), lk - rk, c.op


def _holds(compiled, x: tuple[int, ...]) -> bool:
    for coeffs, const, op in compiled:
        val = const
        for i, v in coeffs:
            val += v * x[i]
        if op == CompOp.LT and not val < 0:
            return False
        if op == CompOp.LE and not val <= 0:
            return False
        if op == CompOp.GT and not val > 0:
            return False
        if op == CompOp.GE and not val >= 0:
            return False
        if op == CompOp.EQ and val != 0:
            return False
    return True


@dataclass
class ExplicitResult:
    holds: bool
    product_states: int
    markings: int
    fair_edges: int
    max_rank: int | None = None
    distinct_ranks: int | None = None
    rank: dict[tuple[str, tuple[int, ...]], int] = field(default_factory=dict, repr=False)


def _product(result: ParseResult):
    """Variables, initial marking, program successor function and automaton edges by state."""
    variables = sorted({a.var for c in result.commands for a in c.assignments}
                       | set(result.types) | {_v for c in (result.init_condition or [])
                                              for _v in _vars(c)})
    idx = {v: i for i, v in enumerate(variables)}

    # Initial states: the init condition bounds every variable (`var == c`, or a box of
    # `var <= c` / `var >= c` together with the variable's type); all points of the box that
    # satisfy the remaining init constraints are enumerated.
    lo = [None] * len(variables)
    hi = [None] * len(variables)
    for v, t in (result.types or {}).items():
        if v in idx:
            lo[idx[v]], hi[idx[v]] = t.min_value, t.max_value
    others = []  # multi-variable init constraints filter the box's points
    for c in result.init_condition or []:
        coeffs, const, op = _compile_comparison(c, idx)
        if len(coeffs) != 1 or abs(coeffs[0][1]) != 1:
            others.append((coeffs, const, op))
            continue
        (i, a), = coeffs
        if a < 0:  # -x + k op 0  <=>  x - k (flipped op) 0
            const = -const
            op = {CompOp.LE: CompOp.GE, CompOp.GE: CompOp.LE, CompOp.LT: CompOp.GT,
                  CompOp.GT: CompOp.LT}.get(op, op)
        v = -const  # x op v
        if op in (CompOp.EQ, CompOp.LE, CompOp.LT):
            b = v if op != CompOp.LT else v - 1
            hi[i] = b if hi[i] is None else min(hi[i], b)
        if op in (CompOp.EQ, CompOp.GE, CompOp.GT):
            b = v if op != CompOp.GT else v + 1
            lo[i] = b if lo[i] is None else max(lo[i], b)
    if any(x is None for x in lo + hi):
        raise ValueError("init condition does not bound every variable")
    x0s = [tuple(p) for p in itertools.product(*(range(a, b + 1) for a, b in zip(lo, hi)))
           if _holds(others, p)]

    commands = []
    for cmd in result.commands:
        if cmd.havoc:
            raise ValueError("havoc is not supported")
        guard = [_compile_comparison(g, idx) for g in cmd.guards]
        updates = [(idx[a.var], _linear(a.expr, idx)) for a in cmd.assignments]
        commands.append((guard, updates))
    by_state: dict[str, list] = {}
    for t in result.automaton_transitions:
        by_state.setdefault(t.from_state, []).append(
            ([_compile_comparison(g, idx) for g in t.guards], t.to_state, t.is_fair))

    def program_successors(x, with_index=False):
        for ci, (guard, updates) in enumerate(commands):
            if not _holds(guard, x):
                continue
            y = list(x)
            for i, (coeffs, const) in updates:
                y[i] = const + sum(v * x[j] for j, v in coeffs.items())
            yield (ci, tuple(y)) if with_index else tuple(y)

    return variables, x0s, program_successors, by_state


def sample_markings(result: ParseResult, budget: int = 100_000, walk_share: float = 0.5,
                    walk_length: int = 5_000, seed: int = 0):
    """Breadth-first exploration of the product, up to `budget` product states; if BFS does not
    exhaust the product, `walk_share` of the budget goes to random walks from the initial state
    instead, so that deep states (far from the initial marking) are sampled too.

    Returns (variables, {automaton state: set of markings seen there}, indices of the commands
    that fired at least once, complete)."""
    from collections import deque

    variables, x0s, program_successors, by_state = _product(result)
    seen = set()
    per_state: dict[str, set] = {}
    queue = deque()
    for q in result.automaton_initial_states or []:
        for x0 in x0s:
            seen.add((q, x0))
            queue.append((q, x0))
    complete = True
    fired = set()
    bfs_budget = budget
    budget = int(budget * (1 - walk_share))
    while queue:
        q, x = queue.popleft()
        per_state.setdefault(q, set()).add(x)
        enabled = [(to, fair) for guard, to, fair in by_state.get(q, []) if _holds(guard, x)]
        if not enabled:
            continue
        for ci, y in program_successors(x, with_index=True):
            fired.add(ci)
            for to, _ in enabled:
                n = (to, y)
                if n not in seen:
                    if len(seen) >= budget:
                        complete = False
                        continue
                    seen.add(n)
                    queue.append(n)
    if not complete and walk_share > 0:
        import random

        rng = random.Random(seed)
        starts = [(q, x0) for q in result.automaton_initial_states or [] for x0 in x0s]
        while len(seen) < bfs_budget and starts:
            q, x = rng.choice(starts)
            for _ in range(walk_length):
                enabled = [(to, fair) for guard, to, fair in by_state.get(q, []) if _holds(guard, x)]
                succs = list(program_successors(x, with_index=True)) if enabled else []
                if not succs:
                    break
                ci, y = rng.choice(succs)
                fired.add(ci)
                q, x = rng.choice(enabled)[0], y
                per_state.setdefault(q, set()).add(x)
                seen.add((q, x))
                if len(seen) >= bfs_budget:
                    break
    return variables, per_state, fired, complete


def check(result: ParseResult, budget: int = 2_000_000, keep_rank: bool = False) -> ExplicitResult:
    variables, x0s, program_successors, by_state = _product(result)

    # Explore the product; nodes are numbered in discovery order.
    node_id: dict[tuple[str, tuple[int, ...]], int] = {}
    nodes: list[tuple[str, tuple[int, ...]]] = []
    succ: list[list[tuple[int, bool]]] = []
    markings = set()

    def intern(n):
        i = node_id.get(n)
        if i is None:
            if len(nodes) >= budget:
                raise TooLarge(f"more than {budget} product states")
            i = node_id[n] = len(nodes)
            nodes.append(n)
            succ.append([])
            stack.append(i)
        return i

    stack: list[int] = []
    for q in result.automaton_initial_states or []:
        for x0 in x0s:
            intern((q, x0))
    while stack:
        i = stack.pop()
        q, x = nodes[i]
        markings.add(x)
        enabled = [(to, fair) for guard, to, fair in by_state.get(q, []) if _holds(guard, x)]
        if not enabled:
            continue
        for y in program_successors(x):
            for to, fair in enabled:
                succ[i].append((intern((to, y)), fair))

    comp = _tarjan(succ)
    fair_edges = sum(fair for edges in succ for _, fair in edges)
    for i, edges in enumerate(succ):
        for j, fair in edges:
            if fair and comp[i] == comp[j]:
                return ExplicitResult(False, len(nodes), len(markings), fair_edges)

    # Tarjan numbers components in reverse topological order (sinks first).
    ncomp = max(comp) + 1 if comp else 0
    members: list[list[int]] = [[] for _ in range(ncomp)]
    for i, c in enumerate(comp):
        members[c].append(i)
    comp_rank = [0] * ncomp
    for c in range(ncomp):
        best = 0
        for i in members[c]:
            for j, fair in succ[i]:
                if comp[j] != c:
                    best = max(best, comp_rank[comp[j]] + int(fair))
        comp_rank[c] = best
    ranks = [comp_rank[c] for c in comp]
    res = ExplicitResult(True, len(nodes), len(markings), fair_edges,
                         max_rank=max(ranks, default=0), distinct_ranks=len(set(ranks)))
    if keep_rank:
        res.rank = {nodes[i]: r for i, r in enumerate(ranks)}
    return res


def _vars(c: Comparison) -> set[str]:
    out = set()

    def walk(e):
        if isinstance(e, Var):
            out.add(e.name)
        elif isinstance(e, BinOp):
            walk(e.left)
            walk(e.right)
        elif isinstance(e, Neg):
            walk(e.expr)

    walk(c.left)
    walk(c.right)
    return out


def _tarjan(succ: list[list[tuple[int, bool]]]) -> list[int]:
    """Iterative Tarjan SCC; returns the component id of every node (reverse topological order)."""
    n = len(succ)
    index = [-1] * n
    low = [0] * n
    on_stack = [False] * n
    comp = [-1] * n
    stack: list[int] = []
    counter = 0
    ncomp = 0
    for root in range(n):
        if index[root] != -1:
            continue
        work = [(root, 0)]
        while work:
            v, pos = work[-1]
            if pos == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack[v] = True
            edges = succ[v]
            if pos < len(edges):
                work[-1] = (v, pos + 1)
                w = edges[pos][0]
                if index[w] == -1:
                    work.append((w, 0))
                elif on_stack[w]:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
            if low[v] == index[v]:
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp[w] = ncomp
                    if w == v:
                        break
                ncomp += 1
    return comp
