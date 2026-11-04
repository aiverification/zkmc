"""Encode TLA+ variables as integer slots.

The layout of each variable is inferred from the values it takes in the reachable states (as
dumped by TLC), so every reachable state has an encoding and slot ranges are exact:

- `ConstLayout`: the variable never changes (no slot);
- `IntLayout`: an integer slot holding the value itself (arithmetic stays linear);
- `BoolLayout`: a 0/1 slot;
- `EnumLayout`: a slot holding the index of the value in a table (strings, model values,
  and any other values without more structure), ordered by first appearance;
- `FnLayout`: one sub-layout per domain element (functions, records, tuples);
- `SeqLayout`: a length slot plus one sub-layout per position (sequences of varying length;
  unused positions hold a fixed default so that the encoding is canonical);
- `SetLayout`: one 0/1 slot per element of the universe of all values the set takes.

`read()` gives the symbolic current value (for the evaluator), `encode(v)` the slot values of a
concrete value, and `flatten(v)` the ways of assigning a (symbolic) value to the slots, as
alternatives `(condition, {slot: Lin})`; values outside the layout yield no alternative.
"""

from __future__ import annotations

import itertools
import re

from tla_sym import (BOTTOM, FALSE, TRUE, Atom, Cases, Lin, SBool, SEnum, SFn, SInt, SSet, TooBig, atom, conj,
                     is_concrete, mk_fn, mk_set, neg)
from tla_values import Fn, ModelValue, is_int, show, sort_key

FLATTEN_LIMIT = 4096


class Names:
    """Allocates unique `.gc` identifiers."""

    RESERVED = {"init", "const", "type", "rank", "trans", "automaton_init", "ap", "spec", "true", "inf"}

    def __init__(self):
        self.used = set()

    def new(self, base: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_]", "_", base).strip("_") or "v"
        if base[0].isdigit():
            base = "v" + base
        if base in self.RESERVED or base.startswith("ap_"):
            base = "v_" + base
        name, i = base, 1
        while name in self.used:
            i += 1
            name = f"{base}_{i}"
        self.used.add(name)
        return name


def key_name(k) -> str:
    if isinstance(k, bool):
        return "T" if k else "F"
    if is_int(k):
        return str(k) if k >= 0 else f"m{-k}"
    if isinstance(k, str):
        return k if re.fullmatch(r"[A-Za-z0-9_]{1,16}", k) else ""
    if isinstance(k, ModelValue):
        return k.name if re.fullmatch(r"[A-Za-z0-9_]{1,16}", k.name) else ""
    if isinstance(k, Fn) and k.is_seq() and len(k) <= 3:
        parts = [key_name(x) for x in k.seq()]
        return "_".join(parts) if all(parts) else ""
    return ""


class Layout:
    def slots(self) -> list[str]:
        raise NotImplementedError

    def ranges(self) -> dict:
        raise NotImplementedError

    def values(self) -> dict:
        """slot -> sorted list of values the slot takes (for case splits)."""
        raise NotImplementedError

    def read(self):
        raise NotImplementedError

    def encode(self, v) -> dict | None:
        raise NotImplementedError

    def default(self) -> dict:
        raise NotImplementedError

    def flatten(self, v, ev) -> list:
        raise NotImplementedError

    def describe(self) -> list[str]:
        return []


class ConstLayout(Layout):
    def __init__(self, value):
        self.value = value

    def slots(self):
        return []

    def ranges(self):
        return {}

    def values(self):
        return {}

    def read(self):
        return self.value

    def encode(self, v):
        return {} if ev_equal_concrete(v, self.value) else None

    def default(self):
        return {}

    def flatten(self, v, ev):
        c = ev.equal(v, self.value)
        return [] if c is FALSE else [(c, {})]


class IntLayout(Layout):
    def __init__(self, slot: str, vals):
        self.slot = slot
        self.vals = sorted(set(vals))

    def slots(self):
        return [self.slot]

    def ranges(self):
        return {self.slot: (self.vals[0], self.vals[-1])}

    def values(self):
        return {self.slot: self.vals}

    def read(self):
        return SInt(Lin.var(self.slot))

    def encode(self, v):
        return {self.slot: v} if is_int(v) else None

    def default(self):
        return {self.slot: self.vals[0]}

    def flatten(self, v, ev):
        lin = ev.to_lin(v)
        if lin is not None:
            if lin.is_const() and lin.const not in self.vals:
                return []
            return [(TRUE, {self.slot: lin})]
        return _split_flatten(self, v, ev)


class BoolLayout(Layout):
    def __init__(self, slot: str):
        self.slot = slot

    def slots(self):
        return [self.slot]

    def ranges(self):
        return {self.slot: (0, 1)}

    def values(self):
        return {self.slot: [0, 1]}

    def read(self):
        return SBool(atom(Lin.var(self.slot) - 1, "=="))

    def encode(self, v):
        return {self.slot: int(v)} if isinstance(v, bool) else None

    def default(self):
        return {self.slot: 0}

    def flatten(self, v, ev):
        if v is True or v is False:
            return [(TRUE, {self.slot: Lin({}, int(v))})]
        if isinstance(v, SBool):
            f = v.f
            copy = _bool_copy(f, ev)
            if copy is not None:
                return [(TRUE, {self.slot: copy})]
            return [(f, {self.slot: Lin({}, 1)}), (neg(f), {self.slot: Lin({}, 0)})]
        return _split_flatten(self, v, ev)


def _bool_copy(f, ev) -> Lin | None:
    """If f is `b == 1`, `b == 0` or `b != 1` for a 0/1 slot b, the 0/1 value of f as a Lin."""
    if not isinstance(f, Atom) or len(f.lin.terms) != 1:
        return None
    (s, c), = f.lin.terms.items()
    if c != 1 or set(ev.slot_values.get(s, ())) - {0, 1}:
        return None
    k = -f.lin.const
    if (f.op == "==" and k == 1) or (f.op == "!=" and k == 0):
        return Lin.var(s)
    if (f.op == "==" and k == 0) or (f.op == "!=" and k == 1):
        return Lin({s: -1}, 1)
    return None


class EnumLayout(Layout):
    def __init__(self, slot: str, table: list):
        self.slot = slot
        self.retable(table, table)

    def retable(self, table: list, observed: list):
        """Use `table` for the codes (shared with other slots, so that copies between them stay
        linear); the slot itself only takes the values in `observed`."""
        self.table = tuple(table)
        self.index = {_hkey(v): i for i, v in enumerate(self.table)}
        self.codes = sorted(self.index[_hkey(v)] for v in observed)

    def slots(self):
        return [self.slot]

    def ranges(self):
        return {self.slot: (self.codes[0], self.codes[-1])}

    def values(self):
        return {self.slot: list(self.codes)}

    def read(self):
        return SEnum(self.slot, self.table)

    def encode(self, v):
        i = self.index.get(_hkey(v))
        return None if i is None else {self.slot: i}

    def default(self):
        return {self.slot: self.codes[0]}

    def flatten(self, v, ev):
        if is_concrete(v):
            i = self.index.get(_hkey(v))
            return [] if i is None else [(TRUE, {self.slot: Lin({}, i)})]
        if isinstance(v, SEnum) and v.table == self.table:
            return [(TRUE, {self.slot: Lin.var(v.slot)})]
        return _split_flatten(self, v, ev)

    def describe(self):
        return [f"{self.slot}: " + ", ".join(f"{i}={show(self.table[i])}" for i in self.codes)]


class FnLayout(Layout):
    def __init__(self, subs: dict):
        self.subs = subs  # key -> Layout

    def slots(self):
        return [s for sub in self.subs.values() for s in sub.slots()]

    def ranges(self):
        return {k: v for sub in self.subs.values() for k, v in sub.ranges().items()}

    def values(self):
        return {k: v for sub in self.subs.values() for k, v in sub.values().items()}

    def read(self):
        return mk_fn({k: sub.read() for k, sub in self.subs.items()})

    def encode(self, v):
        if not isinstance(v, Fn) or set(v.keys()) != set(self.subs):
            return None
        out = {}
        for k, sub in self.subs.items():
            e = sub.encode(v[k])
            if e is None:
                return None
            out.update(e)
        return out

    def default(self):
        return {k: v for sub in self.subs.values() for k, v in sub.default().items()}

    def flatten(self, v, ev):
        v = ev.force(v)
        if isinstance(v, Cases):
            return _cases_flatten(self, v, ev)
        if v is BOTTOM:
            return []
        if isinstance(v, (Fn, SFn)):
            m = v._map if isinstance(v, Fn) else v.map
            if set(m) != set(self.subs):
                return []
            return _product([self.subs[k].flatten(m[k], ev) for k in self.subs])
        return _split_flatten(self, v, ev)

    def describe(self):
        return [d for sub in self.subs.values() for d in sub.describe()]


class SeqLayout(Layout):
    def __init__(self, len_slot: str, lengths, elems: list):
        self.len_slot = len_slot
        self.lengths = sorted(set(lengths))
        self.elems = elems  # Layout for positions 1..max

    def slots(self):
        return [self.len_slot] + [s for e in self.elems for s in e.slots()]

    def ranges(self):
        r = {self.len_slot: (self.lengths[0], self.lengths[-1])}
        for e in self.elems:
            r.update(e.ranges())
        return r

    def values(self):
        r = {self.len_slot: self.lengths}
        for e in self.elems:
            r.update(e.values())
        return r

    def read(self):
        alts = []
        for n in self.lengths:
            alts.append((atom(Lin.var(self.len_slot) - n, "=="),
                         mk_fn({i + 1: self.elems[i].read() for i in range(n)})))
        return Cases(alts) if len(alts) > 1 else alts[0][1]

    def encode(self, v):
        if not isinstance(v, Fn) or not v.is_seq() or len(v) not in self.lengths:
            return None
        out = {self.len_slot: len(v)}
        xs = v.seq()
        for i, e in enumerate(self.elems):
            enc = e.encode(xs[i]) if i < len(xs) else e.default()
            if enc is None:
                return None
            out.update(enc)
        return out

    def default(self):
        out = {self.len_slot: self.lengths[0]}
        for e in self.elems:
            out.update(e.default())
        return out

    def flatten(self, v, ev):
        v = ev.force(v)
        if isinstance(v, Cases):
            return _cases_flatten(self, v, ev)
        if v is BOTTOM:
            return []
        if isinstance(v, (Fn, SFn)):
            m = v._map if isinstance(v, Fn) else v.map
            n = len(m)
            if set(m) != set(range(1, n + 1)) or n not in self.lengths:
                return []
            parts = [[(TRUE, {self.len_slot: Lin({}, n)})]]
            for i, e in enumerate(self.elems):
                if i < n:
                    parts.append(e.flatten(m[i + 1], ev))
                else:
                    parts.append([(TRUE, {s: Lin({}, x) for s, x in e.default().items()})])
            return _product(parts)
        return _split_flatten(self, v, ev)

    def describe(self):
        return [d for e in self.elems for d in e.describe()]


class SetLayout(Layout):
    def __init__(self, slots: dict):
        self.slot_of = slots  # element -> slot

    def slots(self):
        return list(self.slot_of.values())

    def ranges(self):
        return {s: (0, 1) for s in self.slot_of.values()}

    def values(self):
        return {s: [0, 1] for s in self.slot_of.values()}

    def read(self):
        return SSet({k: atom(Lin.var(s) - 1, "==") for k, s in self.slot_of.items()})

    def encode(self, v):
        if not isinstance(v, frozenset) or not v <= set(self.slot_of):
            return None
        return {s: int(k in v) for k, s in self.slot_of.items()}

    def default(self):
        return {s: 0 for s in self.slot_of.values()}

    def flatten(self, v, ev):
        v = ev.force(v)
        if isinstance(v, Cases):
            return _cases_flatten(self, v, ev)
        if v is BOTTOM:
            return []
        if isinstance(v, frozenset):
            if not v <= set(self.slot_of):
                return []
            return [(TRUE, {s: Lin({}, int(k in v)) for k, s in self.slot_of.items()})]
        if isinstance(v, SSet):
            cond = conj(*(neg(f) for k, f in v.mem.items() if k not in self.slot_of))
            if cond is FALSE:
                return []
            parts = [[(cond, {})]]
            for k, s in self.slot_of.items():
                f = v.mem.get(k, FALSE)
                if f is TRUE or f is FALSE:
                    parts.append([(TRUE, {s: Lin({}, int(f is TRUE))})])
                    continue
                copy = _bool_copy(f, ev)
                if copy is not None:
                    parts.append([(TRUE, {s: copy})])
                else:
                    parts.append([(f, {s: Lin({}, 1)}), (neg(f), {s: Lin({}, 0)})])
            return _product(parts)
        return _split_flatten(self, v, ev)

    def describe(self):
        return [f"{s}: {show(k)} in set" for k, s in self.slot_of.items()]


def _product(parts: list) -> list:
    size = 1
    for p in parts:
        size *= len(p)
        if size == 0:
            return []
    if size > FLATTEN_LIMIT:
        raise TooBig(f"assignment splits into {size} alternatives")
    out = []
    for combo in itertools.product(*parts):
        c = conj(*(p[0] for p in combo))
        if c is FALSE:
            continue
        a = {}
        for p in combo:
            a.update(p[1])
        out.append((c, a))
    return out


def _cases_flatten(layout: Layout, v: Cases, ev) -> list:
    out = []
    for c, x in v.alts:
        for c2, a in layout.flatten(x, ev):
            cc = conj(c, c2)
            if cc is not FALSE:
                out.append((cc, a))
    return out


def _split_flatten(layout: Layout, v, ev) -> list:
    out = []
    for c, x in ev.concretize(v):
        enc = layout.encode(x)
        if enc is not None:
            out.append((c, {s: Lin({}, n) for s, n in enc.items()}))
    return out


def _hkey(v):
    return (type(v).__name__ if not isinstance(v, bool) else "bool", v)


def ev_equal_concrete(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return type(a) == type(b) and a == b


MAX_FN_KEYS = 256
MAX_SEQ_LEN = 64
MAX_SET_UNIVERSE = 512


def infer(prefix: str, vals: list, names: Names) -> Layout:
    """Layout for a variable (or component) taking the given values (in first-appearance order)."""
    uniq = []
    seen = set()
    for v in vals:
        k = _hkey(v)
        if k not in seen:
            seen.add(k)
            uniq.append(v)
    if len(uniq) == 1:
        return ConstLayout(uniq[0])
    if all(isinstance(v, bool) for v in uniq):
        return BoolLayout(names.new(prefix))
    if all(is_int(v) for v in uniq):
        return IntLayout(names.new(prefix), uniq)
    if all(isinstance(v, Fn) for v in uniq):
        doms = {v.domain() for v in uniq}
        if len(doms) == 1:
            dom = next(iter(doms))
            if 0 < len(dom) <= MAX_FN_KEYS:
                keys = sorted(dom, key=sort_key)
                subs = {}
                for i, k in enumerate(keys):
                    kn = key_name(k) or f"k{i}"
                    subs[k] = infer(f"{prefix}_{kn}", [v[k] for v in vals], names)
                return FnLayout(subs)
        if all(v.is_seq() for v in uniq):
            maxlen = max(len(v) for v in uniq)
            if maxlen <= MAX_SEQ_LEN:
                len_slot = names.new(f"{prefix}_len")
                elems = []
                for i in range(maxlen):
                    ev = [v.seq()[i] for v in vals if len(v) > i]
                    elems.append(infer(f"{prefix}_{i + 1}", ev, names))
                return SeqLayout(len_slot, [len(v) for v in uniq], elems)
    if all(isinstance(v, frozenset) for v in uniq):
        universe = []
        useen = set()
        for v in vals:
            for x in sorted(v, key=sort_key):
                k = _hkey(x)
                if k not in useen:
                    useen.add(k)
                    universe.append(x)
        if len(universe) <= MAX_SET_UNIVERSE:
            slots = {}
            for i, x in enumerate(universe):
                kn = key_name(x)
                slots[x] = names.new(f"{prefix}_{kn}" if kn else f"{prefix}_e{i}")
            return SetLayout(slots)
    return EnumLayout(names.new(prefix), uniq)


def read_after(layout: Layout, assign: dict):
    """The symbolic value of a variable after a command that assigns `assign` (slot -> Lin, in
    terms of the slots before the command); unassigned slots keep their value."""
    def lin(s):
        return assign.get(s, Lin.var(s))

    if isinstance(layout, ConstLayout):
        return layout.value
    if isinstance(layout, IntLayout):
        x = lin(layout.slot)
        return x.const if x.is_const() else SInt(x)
    if isinstance(layout, BoolLayout):
        x = lin(layout.slot)
        if x.is_const():
            return x.const == 1
        return SBool(atom(x - 1, "=="))
    if isinstance(layout, EnumLayout):
        x = lin(layout.slot)
        if x.is_const():
            return layout.table[x.const] if 0 <= x.const < len(layout.table) else BOTTOM
        if x == Lin.var(layout.slot):
            return SEnum(layout.slot, layout.table)
        return Cases([(atom(x - i, "=="), v) for i, v in enumerate(layout.table)])
    if isinstance(layout, FnLayout):
        return mk_fn({k: read_after(sub, assign) for k, sub in layout.subs.items()})
    if isinstance(layout, SeqLayout):
        n = lin(layout.len_slot)
        alts = []
        for k in layout.lengths:
            c = atom(n - k, "==")
            if c is not FALSE:
                alts.append((c, mk_fn({i + 1: read_after(layout.elems[i], assign) for i in range(k)})))
        return alts[0][1] if len(alts) == 1 and alts[0][0] is TRUE else Cases(alts)
    if isinstance(layout, SetLayout):
        return mk_set({k: atom(lin(s) - 1, "==") for k, s in layout.slot_of.items()})
    raise TypeError(layout)


def enum_layouts(layout: Layout) -> list:
    if isinstance(layout, EnumLayout):
        return [layout]
    if isinstance(layout, FnLayout):
        return [e for sub in layout.subs.values() for e in enum_layouts(sub)]
    if isinstance(layout, SeqLayout):
        return [e for sub in layout.elems for e in enum_layouts(sub)]
    return []


def share_tables(layouts: list):
    """Give enumeration slots whose values overlap one common code table."""
    enums = [e for lay in layouts for e in enum_layouts(lay)]
    parent = list(range(len(enums)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner = {}
    for i, e in enumerate(enums):
        for v in e.table:
            j = owner.setdefault(_hkey(v), i)
            parent[find(i)] = find(j)
    groups: dict = {}
    for i, e in enumerate(enums):
        groups.setdefault(find(i), []).append(e)
    for members in groups.values():
        if len(members) < 2:
            continue
        table, seen = [], set()
        for e in members:
            for v in e.table:
                if _hkey(v) not in seen:
                    seen.add(_hkey(v))
                    table.append(v)
        for e in members:
            e.retable(table, list(e.table))
