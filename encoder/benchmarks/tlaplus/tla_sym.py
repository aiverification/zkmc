"""Symbolic values over integer slots, for grounding TLA+ actions into guarded commands.

A TLA+ state is encoded as a vector of integer *slots* (see `layout.py`). Expressions over
the current state evaluate to

- concrete values (`tla_values`), when they do not depend on the state;
- `SInt(lin)`: an integer given by a linear expression over slots;
- `SBool(f)`: a boolean given by a formula over linear atoms;
- `SEnum(slot, table)`: the value `table[slot]` of an enumeration-coded slot;
- `SFn(map)`: a function with a concrete domain and symbolic values (records, tuples);
- `SSet(mem)`: a set with a concrete universe and symbolic membership formulas;
- `Cases(alts)`: one of several values, under mutually exclusive conditions;
- `BOTTOM`: undefined (TLC would report an error); paths producing it are dropped.

Formulas are kept in negation normal form over atoms `lin <= 0`, `lin == 0`, `lin != 0`.
`to_dnf` turns them into cubes, using the slots' value ranges to simplify.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd

from tla_values import Fn, sort_key


class TooBig(Exception):
    """A case split or DNF exceeds its budget."""


# --------------------------------------------------------------------------------------------
# Linear expressions
# --------------------------------------------------------------------------------------------

class Lin:
    """`const + sum(coef * slot)` over integer slots."""

    __slots__ = ("terms", "const", "_hash")

    def __init__(self, terms: dict | None = None, const: int = 0):
        self.terms = {k: v for k, v in (terms or {}).items() if v}
        self.const = const
        self._hash = None

    @staticmethod
    def var(name: str) -> "Lin":
        return Lin({name: 1})

    def is_const(self) -> bool:
        return not self.terms

    def __add__(self, o: "Lin | int") -> "Lin":
        if isinstance(o, int):
            return Lin(self.terms, self.const + o)
        t = dict(self.terms)
        for k, v in o.terms.items():
            t[k] = t.get(k, 0) + v
        return Lin(t, self.const + o.const)

    def __neg__(self) -> "Lin":
        return Lin({k: -v for k, v in self.terms.items()}, -self.const)

    def __sub__(self, o: "Lin | int") -> "Lin":
        return self + (-o if isinstance(o, Lin) else -o)

    def scale(self, c: int) -> "Lin":
        return Lin({k: v * c for k, v in self.terms.items()}, self.const * c)

    def key(self):
        return (tuple(sorted(self.terms.items())), self.const)

    def __eq__(self, o):
        return isinstance(o, Lin) and self.key() == o.key()

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(self.key())
        return self._hash

    def render(self) -> str:
        parts = []
        for k, v in sorted(self.terms.items()):
            if v == 1:
                parts.append(("+", k))
            elif v == -1:
                parts.append(("-", k))
            elif v > 0:
                parts.append(("+", f"{v}*{k}"))
            else:
                parts.append(("-", f"{-v}*{k}"))
        if self.const > 0 or not parts:
            parts.append(("+", str(self.const)))
        elif self.const < 0:
            parts.append(("-", str(-self.const)))
        s = ("-" if parts[0][0] == "-" else "") + parts[0][1]
        for sign, t in parts[1:]:
            s += f" {sign} {t}"
        return s

    def __repr__(self):
        return self.render()


# --------------------------------------------------------------------------------------------
# Formulas (NNF)
# --------------------------------------------------------------------------------------------

class Formula:
    __slots__ = ()


class _Const(Formula):
    __slots__ = ("value",)

    def __init__(self, value: bool):
        self.value = value

    def __repr__(self):
        return "TRUE" if self.value else "FALSE"


TRUE = _Const(True)
FALSE = _Const(False)


class Atom(Formula):
    """`lin op 0` with op in {'<=', '==', '!='}; normalized (gcd 1, leading coefficient > 0
    for equalities)."""

    __slots__ = ("lin", "op", "_hash")

    def __init__(self, lin: Lin, op: str):
        self.lin, self.op = lin, op
        self._hash = hash((lin, op))

    def __eq__(self, o):
        return isinstance(o, Atom) and self.op == o.op and self.lin == o.lin

    def __hash__(self):
        return self._hash

    def __repr__(self):
        return f"({self.lin.render()} {self.op} 0)"


class And(Formula):
    __slots__ = ("args", "_hash")

    def __init__(self, args: tuple):
        self.args = args
        self._hash = hash(("and", frozenset(args)))

    def __eq__(self, o):
        return isinstance(o, And) and frozenset(self.args) == frozenset(o.args)

    def __hash__(self):
        return self._hash

    def __repr__(self):
        return "(" + " /\\ ".join(map(repr, self.args)) + ")"


class Or(Formula):
    __slots__ = ("args", "_hash")

    def __init__(self, args: tuple):
        self.args = args
        self._hash = hash(("or", frozenset(args)))

    def __eq__(self, o):
        return isinstance(o, Or) and frozenset(self.args) == frozenset(o.args)

    def __hash__(self):
        return self._hash

    def __repr__(self):
        return "(" + " \\/ ".join(map(repr, self.args)) + ")"


def atom(lin: Lin, op: str) -> Formula:
    """`lin op 0`, normalized; constant atoms fold to TRUE/FALSE."""
    if lin.is_const():
        c = lin.const
        return TRUE if {"<=": c <= 0, "==": c == 0, "!=": c != 0}[op] else FALSE
    g = 0
    for v in lin.terms.values():
        g = gcd(g, abs(v))
    if op == "<=":
        # sum(a x) + c <= 0  <=>  sum(a/g x) + ceil(c/g) <= 0
        lin = Lin({k: v // g for k, v in lin.terms.items()}, -((-lin.const) // g))
    else:
        if lin.const % g:
            return FALSE if op == "==" else TRUE
        lin = Lin({k: v // g for k, v in lin.terms.items()}, lin.const // g)
        if lin.terms[min(lin.terms)] < 0:
            lin = -lin
    return Atom(lin, op)


def le(a: Lin, b: Lin) -> Formula:
    return atom(a - b, "<=")


def lt(a: Lin, b: Lin) -> Formula:
    return atom(a - b + 1, "<=")


def eq(a: Lin, b: Lin) -> Formula:
    return atom(a - b, "==")


def conj(*fs: Formula) -> Formula:
    out, seen = [], set()
    for f in fs:
        items = f.args if isinstance(f, And) else (f,)
        for g in items:
            if g is FALSE:
                return FALSE
            if g is TRUE or g in seen:
                continue
            seen.add(g)
            out.append(g)
    if not out:
        return TRUE
    return out[0] if len(out) == 1 else And(tuple(out))


def disj(*fs: Formula) -> Formula:
    out, seen = [], set()
    for f in fs:
        items = f.args if isinstance(f, Or) else (f,)
        for g in items:
            if g is TRUE:
                return TRUE
            if g is FALSE or g in seen:
                continue
            seen.add(g)
            out.append(g)
    if not out:
        return FALSE
    return out[0] if len(out) == 1 else Or(tuple(out))


def neg(f: Formula) -> Formula:
    if f is TRUE:
        return FALSE
    if f is FALSE:
        return TRUE
    if isinstance(f, Atom):
        if f.op == "<=":
            return atom(-f.lin + 1, "<=")  # not(l <= 0) <=> -l + 1 <= 0
        return Atom(f.lin, "!=" if f.op == "==" else "==")
    if isinstance(f, And):
        return disj(*(neg(g) for g in f.args))
    return conj(*(neg(g) for g in f.args))


def iff(a: Formula, b: Formula) -> Formula:
    if a is TRUE:
        return b
    if b is TRUE:
        return a
    if a is FALSE:
        return neg(b)
    if b is FALSE:
        return neg(a)
    if a == b:
        return TRUE
    return disj(conj(a, b), conj(neg(a), neg(b)))


def ite(c: Formula, a: Formula, b: Formula) -> Formula:
    if c is TRUE:
        return a
    if c is FALSE:
        return b
    return disj(conj(c, a), conj(neg(c), b))


def slots_of(f: Formula, out: set | None = None) -> set:
    out = set() if out is None else out
    if isinstance(f, Atom):
        out.update(f.lin.terms)
    elif isinstance(f, (And, Or)):
        for g in f.args:
            slots_of(g, out)
    return out


# --------------------------------------------------------------------------------------------
# Symbolic values
# --------------------------------------------------------------------------------------------

class _Bottom:
    def __repr__(self):
        return "BOTTOM"


BOTTOM = _Bottom()


@dataclass(frozen=True)
class SInt:
    lin: Lin


@dataclass(frozen=True)
class SBool:
    f: Formula


@dataclass(frozen=True)
class SEnum:
    slot: str
    table: tuple  # code -> concrete value


class SFn:
    """A function with a concrete domain whose values may be symbolic."""

    __slots__ = ("map",)

    def __init__(self, mapping: dict):
        self.map = mapping

    def __repr__(self):
        return "SFn(" + ", ".join(f"{k!r}: {v!r}" for k, v in self.map.items()) + ")"


class SSet:
    """A set over a concrete universe with symbolic membership."""

    __slots__ = ("mem",)

    def __init__(self, mem: dict):
        self.mem = mem  # element -> Formula

    def __repr__(self):
        return "SSet(" + ", ".join(f"{k!r}: {v!r}" for k, v in self.mem.items()) + ")"


class Cases:
    __slots__ = ("alts",)

    def __init__(self, alts: list):
        self.alts = alts  # [(Formula, value)]

    def __repr__(self):
        return "Cases(" + "; ".join(f"{c!r} -> {v!r}" for c, v in self.alts) + ")"


SYMBOLIC = (SInt, SBool, SEnum, SFn, SSet, Cases, _Bottom)


def is_concrete(v) -> bool:
    return not isinstance(v, SYMBOLIC)


def mk_int(lin: Lin):
    return lin.const if lin.is_const() else SInt(lin)


def mk_bool(f: Formula):
    if f is TRUE:
        return True
    if f is FALSE:
        return False
    return SBool(f)


def mk_fn(mapping: dict):
    if all(is_concrete(v) for v in mapping.values()):
        return Fn(mapping)
    return SFn(mapping)


def mk_set(mem: dict):
    mem = {k: f for k, f in mem.items() if f is not FALSE}
    if all(f is TRUE for f in mem.values()):
        return frozenset(mem)
    return SSet(mem)


def mk_cases(alts) -> object:
    """Normalize a list of (condition, value): flatten nested Cases, drop impossible
    alternatives, merge equal concrete values."""
    flat = []
    for c, v in alts:
        if c is FALSE:
            continue
        if isinstance(v, Cases):
            for c2, v2 in v.alts:
                cc = conj(c, c2)
                if cc is not FALSE:
                    flat.append((cc, v2))
        else:
            flat.append((c, v))
    if not flat:
        return BOTTOM
    merged: list = []
    index: dict = {}
    for c, v in flat:
        if is_concrete(v):
            try:
                key = ("c", v)
                hash(key)
            except TypeError:
                key = None
        elif v is BOTTOM:
            key = ("b",)
        else:
            key = None
        if key is not None and key in index:
            i = index[key]
            merged[i] = (disj(merged[i][0], c), v)
        else:
            if key is not None:
                index[key] = len(merged)
            merged.append((c, v))
    if len(merged) == 1:
        return merged[0][1]
    # Cases over ints that are just `slot == k -> k` collapse back into a linear value.
    if all(isinstance(v, int) and not isinstance(v, bool) for _, v in merged):
        slot = _identity_slot(merged)
        if slot is not None:
            return SInt(Lin.var(slot))
    return Cases(merged)


def _identity_slot(alts) -> str | None:
    slot = None
    for c, v in alts:
        conds = c.args if isinstance(c, Or) else (c,)
        for a in conds:
            if not (isinstance(a, Atom) and a.op == "==" and len(a.lin.terms) == 1):
                return None
            (s, coef), = a.lin.terms.items()
            if coef != 1 or -a.lin.const != v or (slot is not None and s != slot):
                return None
            slot = s
    return slot


# --------------------------------------------------------------------------------------------
# DNF with slot ranges
# --------------------------------------------------------------------------------------------

class Cube:
    """A conjunction: per-slot intervals plus multi-slot atoms."""

    __slots__ = ("bounds", "atoms")

    def __init__(self, bounds: dict, atoms: frozenset):
        self.bounds = bounds  # slot -> (lo, hi), only slots narrower than their range
        self.atoms = atoms    # multi-slot Atoms with op '<=' or '=='

    def key(self):
        return (tuple(sorted(self.bounds.items())), self.atoms)

    def __eq__(self, o):
        return isinstance(o, Cube) and self.key() == o.key()

    def __hash__(self):
        return hash(self.key())

    def __repr__(self):
        return f"Cube({self.bounds}, {set(self.atoms)})"


class Ranges:
    """Value ranges of slots: slot -> (lo, hi)."""

    def __init__(self, ranges: dict):
        self.r = ranges

    def get(self, slot: str):
        return self.r[slot]


def _interval(lin: Lin, bounds: dict, ranges: Ranges):
    lo = hi = lin.const
    for k, v in lin.terms.items():
        a, b = bounds.get(k) or ranges.get(k)
        if v > 0:
            lo, hi = lo + v * a, hi + v * b
        else:
            lo, hi = lo + v * b, hi + v * a
    return lo, hi


def _add_atom(cube: Cube, a: Atom, ranges: Ranges) -> list[Cube]:
    """Conjoin an atom to a cube; returns 0, 1 or 2 cubes (`!=` may split)."""
    lo, hi = _interval(a.lin, cube.bounds, ranges)
    op = a.op
    if op == "<=":
        if hi <= 0:
            return [cube]
        if lo > 0:
            return []
    elif op == "==":
        if lo > 0 or hi < 0:
            return []
        if lo == hi == 0:
            return [cube]
    else:
        if lo > 0 or hi < 0:
            return [cube]
        if lo == hi == 0:
            return []
    if len(a.lin.terms) == 1:
        (s, c), = a.lin.terms.items()
        k = a.lin.const
        a_lo, a_hi = cube.bounds.get(s) or ranges.get(s)
        if op == "<=":  # c*s + k <= 0
            if c > 0:
                a_hi = min(a_hi, (-k) // c)
            else:
                a_lo = max(a_lo, _ceil_div(k, -c))
            return [_with_bound(cube, s, a_lo, a_hi, ranges)] if a_lo <= a_hi else []
        if (-k) % c:
            return [] if op == "==" else [cube]
        v = (-k) // c
        if op == "==":
            return [_with_bound(cube, s, v, v, ranges)] if a_lo <= v <= a_hi else []
        out = []
        if a_lo <= v - 1:
            out.append(_with_bound(cube, s, a_lo, min(a_hi, v - 1), ranges))
        if v + 1 <= a_hi:
            out.append(_with_bound(cube, s, max(a_lo, v + 1), a_hi, ranges))
        return out
    if op == "!=":
        return (_add_atom(cube, Atom(a.lin + 1, "<="), ranges)
                + _add_atom(cube, Atom(-a.lin + 1, "<="), ranges))
    return [Cube(cube.bounds, cube.atoms | {a})]


def _ceil_div(a: int, b: int) -> int:
    return -((-a) // b)


def _with_bound(cube: Cube, s: str, lo: int, hi: int, ranges: Ranges) -> Cube:
    b = dict(cube.bounds)
    if (lo, hi) == ranges.get(s):
        b.pop(s, None)
    else:
        b[s] = (lo, hi)
    return Cube(b, cube.atoms)


def _and_cubes(x: Cube, y: Cube, ranges: Ranges) -> list[Cube]:
    out = [x]
    for s, (lo, hi) in y.bounds.items():
        nxt = []
        for c in out:
            a_lo, a_hi = c.bounds.get(s) or ranges.get(s)
            lo2, hi2 = max(a_lo, lo), min(a_hi, hi)
            if lo2 <= hi2:
                nxt.append(_with_bound(c, s, lo2, hi2, ranges))
        out = nxt
    for a in y.atoms:
        out = [d for c in out for d in _add_atom(c, a, ranges)]
    return out


TRUE_CUBE = Cube({}, frozenset())


def to_dnf(f: Formula, ranges: Ranges, limit: int = 4096, total: int = 20000) -> list[Cube]:
    """Cubes whose disjunction equals `f` on the slots' ranges (empty list: unsatisfiable).

    The formula is first simplified under the slot bounds its conjunctions imply. Distributing
    conjunctions over disjunctions can still blow up when the result is small (typically for the
    negation of a large DNF); then the formula is instead expanded on the values of one slot at
    a time (Shannon expansion), simplifying each cofactor."""
    f = simplify(f, ranges)
    try:
        return _merge(_dnf(f, ranges, limit), ranges)
    except TooBig:
        pass
    return _merge(_shannon(f, ranges, 64, total, {}), ranges)


def _decide(a: Atom, bounds: dict, ranges: Ranges) -> Formula:
    lo, hi = _interval(a.lin, bounds, ranges)
    if a.op == "<=":
        return TRUE if hi <= 0 else FALSE if lo > 0 else a
    if a.op == "==":
        return FALSE if lo > 0 or hi < 0 else TRUE if lo == hi == 0 else a
    return TRUE if lo > 0 or hi < 0 else FALSE if lo == hi == 0 else a


def simplify(f: Formula, ranges: Ranges, bounds: dict | None = None) -> Formula:
    """f simplified under slot bounds (slot -> (lo, hi)): atoms decided by the bounds become
    TRUE/FALSE, and single-slot atoms of a conjunction narrow the bounds for its siblings."""
    bounds = bounds or {}
    if isinstance(f, Atom):
        return _decide(f, bounds, ranges)
    if isinstance(f, Or):
        return disj(*(simplify(g, ranges, bounds) for g in f.args))
    if not isinstance(f, And):
        return f
    b = dict(bounds)
    args = list(f.args)
    while True:
        rest, changed = [], False
        for g in args:
            if isinstance(g, Atom) and len(g.lin.terms) == 1 and g.op != "!=":
                cubes = _add_atom(Cube(b, frozenset()), g, ranges)
                if not cubes:
                    return FALSE
                if cubes[0].bounds != b:
                    b = cubes[0].bounds
                    changed = True
            else:
                rest.append(g)
        args = []
        for g in rest:
            h = simplify(g, ranges, b)
            if h is FALSE:
                return FALSE
            if h is TRUE:
                continue
            if isinstance(h, And):
                args.extend(h.args)
                changed = True
            else:
                if isinstance(h, Atom) and len(h.lin.terms) == 1 and h.op != "!=":
                    changed = True
                args.append(h)
        if not changed:
            break
    narrowed = []
    for slot, (lo, hi) in b.items():
        if bounds.get(slot) == (lo, hi):
            continue
        v = Lin.var(slot)
        if lo == hi:
            narrowed.append(atom(v - lo, "=="))
        else:
            r_lo, r_hi = bounds.get(slot) or ranges.get(slot)
            if lo > r_lo:
                narrowed.append(atom(-v + lo, "<="))
            if hi < r_hi:
                narrowed.append(atom(v - hi, "<="))
    return conj(*narrowed, *args)


def subst(f: Formula, s: str, v: int) -> Formula:
    """f with slot s replaced by the constant v."""
    if isinstance(f, Atom):
        c = f.lin.terms.get(s)
        if c is None:
            return f
        return atom(Lin({k: x for k, x in f.lin.terms.items() if k != s}, f.lin.const + c * v), f.op)
    if isinstance(f, And):
        return conj(*(subst(g, s, v) for g in f.args))
    if isinstance(f, Or):
        return disj(*(subst(g, s, v) for g in f.args))
    return f


def _shannon(f: Formula, ranges: Ranges, limit: int, total: int, memo: dict) -> list[Cube]:
    if f is TRUE:
        return [TRUE_CUBE]
    if f is FALSE:
        return []
    hit = memo.get(f)
    if hit is not None:
        return hit
    try:
        out = _merge(_dnf(f, ranges, limit), ranges)
    except TooBig:
        counts: dict = {}
        _count_slots(f, counts)
        s = max(counts, key=lambda k: (counts[k], -(ranges.get(k)[1] - ranges.get(k)[0]), k))
        lo, hi = ranges.get(s)
        if hi - lo + 1 > 256:
            raise TooBig(f"Shannon expansion over {s} with {hi - lo + 1} values")
        out = []
        for v in range(lo, hi + 1):
            for c in _shannon(simplify(subst(f, s, v), ranges), ranges, limit, total, memo):
                out.append(_with_bound(c, s, v, v, ranges))
            if len(out) > total:
                raise TooBig(f"DNF over {total} cubes")
        out = _merge(out, ranges)
    memo[f] = out
    return out


def _count_slots(f: Formula, counts: dict):
    if isinstance(f, Atom):
        for k in f.lin.terms:
            counts[k] = counts.get(k, 0) + 1
    elif isinstance(f, (And, Or)):
        for g in f.args:
            _count_slots(g, counts)


def _dnf(f: Formula, ranges: Ranges, limit: int) -> list[Cube]:
    if f is TRUE:
        return [TRUE_CUBE]
    if f is FALSE:
        return []
    if isinstance(f, Atom):
        return _add_atom(TRUE_CUBE, f, ranges)
    if isinstance(f, Or):
        out = []
        for g in f.args:
            out.extend(_dnf(g, ranges, limit))
            if len(out) > limit:
                raise TooBig(f"DNF over {limit} cubes")
        return _dedupe(out)
    # And: single-slot atoms first (they only narrow intervals), then the rest by size.
    args = sorted(f.args, key=lambda g: (not isinstance(g, Atom), isinstance(g, Or) and len(g.args)))
    cubes = [TRUE_CUBE]
    for g in args:
        if isinstance(g, Atom):
            cubes = [d for c in cubes for d in _add_atom(c, g, ranges)]
        else:
            sub = _dnf(g, ranges, limit)
            nxt = []
            for c in cubes:
                for d in sub:
                    nxt.extend(_and_cubes(c, d, ranges))
                    if len(nxt) > limit:
                        raise TooBig(f"DNF over {limit} cubes")
            cubes = _dedupe(nxt)
        if not cubes:
            return []
    return cubes


def _dedupe(cubes: list[Cube]) -> list[Cube]:
    seen, out = set(), []
    for c in cubes:
        k = c.key()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _subsumes(a: Cube, b: Cube, ranges: Ranges) -> bool:
    """Does cube `a` contain cube `b`?"""
    if not a.atoms <= b.atoms:
        return False
    for s, (lo, hi) in a.bounds.items():
        b_lo, b_hi = b.bounds.get(s) or ranges.get(s)
        if b_lo < lo or b_hi > hi:
            return False
    return True


def _merge(cubes: list[Cube], ranges: Ranges) -> list[Cube]:
    """Merge cubes that differ in one slot's interval when the intervals touch, and drop
    subsumed cubes."""
    cubes = _dedupe(cubes)
    changed = True
    while changed and len(cubes) > 1:
        changed = False
        groups: dict = {}
        for c in cubes:
            for s in set(c.bounds) | {None}:
                rest = tuple(sorted((k, v) for k, v in c.bounds.items() if k != s))
                groups.setdefault((s, rest, c.atoms), []).append(c)
        used = set()
        out = []
        for (s, rest, atoms), group in groups.items():
            if s is None or len(group) < 2:
                continue
            group = [c for c in group if id(c) not in used]
            if len(group) < 2:
                continue
            ivs = sorted((c.bounds.get(s) or ranges.get(s), c) for c in group)
            cur_lo, cur_hi = ivs[0][0]
            members = [ivs[0][1]]
            for (lo, hi), c in ivs[1:]:
                if lo <= cur_hi + 1:
                    cur_hi = max(cur_hi, hi)
                    members.append(c)
                    continue
                if len(members) > 1:
                    out.append(_with_bound(Cube(dict(rest), atoms), s, cur_lo, cur_hi, ranges))
                    used.update(id(m) for m in members)
                    changed = True
                cur_lo, cur_hi, members = lo, hi, [c]
            if len(members) > 1:
                out.append(_with_bound(Cube(dict(rest), atoms), s, cur_lo, cur_hi, ranges))
                used.update(id(m) for m in members)
                changed = True
        cubes = _dedupe(out + [c for c in cubes if id(c) not in used])
    # Subsumption.
    keep = []
    for i, c in enumerate(cubes):
        if any(j != i and _subsumes(d, c, ranges) and (not _subsumes(c, d, ranges) or j < i)
               for j, d in enumerate(cubes)):
            continue
        keep.append(c)
    return keep


def cube_formula(c: Cube) -> Formula:
    parts = []
    for s, (lo, hi) in c.bounds.items():
        v = Lin.var(s)
        if lo == hi:
            parts.append(atom(v - lo, "=="))
        else:
            parts.append(atom(v - hi, "<="))
            parts.append(atom(-v + lo, "<="))
    return conj(*parts, *c.atoms)


def render_cube(c: Cube, ranges: Ranges) -> str:
    """`.gc` guard text; bounds equal to a slot's range are implied by its type."""
    parts = []
    for s, (lo, hi) in sorted(c.bounds.items()):
        r_lo, r_hi = ranges.get(s)
        if lo == hi:
            parts.append(f"{s} == {lo}")
            continue
        if lo > r_lo:
            parts.append(f"{s} >= {lo}")
        if hi < r_hi:
            parts.append(f"{s} <= {hi}")
    for a in sorted(c.atoms, key=lambda a: (a.lin.key(), a.op)):
        parts.append(_render_atom(a))
    return " && ".join(parts) if parts else "true"


def _render_atom(a: Atom) -> str:
    pos = Lin({k: v for k, v in a.lin.terms.items() if v > 0})
    negp = Lin({k: -v for k, v in a.lin.terms.items() if v < 0}, -a.lin.const)
    if not pos.terms:
        pos, negp = negp, pos
        op = {"<=": ">=", "==": "=="}[a.op]
    else:
        op = a.op
    return f"{pos.render()} {op} {negp.render()}"


def value_key(v):
    """Hashable, ordered key for concrete values (for dict keys and sorting)."""
    return sort_key(v)
