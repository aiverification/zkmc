"""Translate a Model Checking Contest (MCC) P/T net + LTL formula into a `.gc` program.

A place/transition net is exactly a linear guarded-command program:

- each **place** becomes an integer variable (its token count);
- each **transition** t becomes `[] p >= pre(p,t) && ... -> p = p + post(p,t) - pre(p,t); ...`;
- the **initial marking** becomes the init condition.

MCC LTL formulas (`LTLCardinality.xml`, `LTLFireability.xml`) are path formulas under `A`.
Their atoms become `ap` bindings and the formula becomes a Spot `spec:`:

- `integer-le(a, b)` with `a`, `b` either `tokens-count(places)` (a sum) or a constant
  becomes `ap aK := a <= b`;
- `is-fireable(t1, ..., tn)` becomes `(eK1 | ... | eKn)` with `ap eK := p >= pre(p,tK) && ...`.

MCC treats deadlocks as infinite stuttering, whereas `.gc` runs end at a deadlock. The two
semantics coincide on deadlock-free nets. For other nets, `deadlock_cubes` adds stutter commands
`[] cube -> p0 = p0` whose guards cover exactly the dead markings within the net's place
invariants (so every reachable dead marking stutters, and no live reachable marking does).
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def _text_int(el: ET.Element | None, default: int) -> int:
    if el is None:
        return default
    for t in el.iter():
        if _local(t.tag) == "text" and t.text and t.text.strip():
            return int(t.text.strip())
    return default


@dataclass
class PTNet:
    places: list[str]                                  # PNML place ids, in declaration order
    initial: dict[str, int]                            # place id -> initial tokens
    transitions: list[str]                             # PNML transition ids
    pre: dict[str, dict[str, int]] = field(default_factory=dict)   # t -> {place: weight}
    post: dict[str, dict[str, int]] = field(default_factory=dict)  # t -> {place: weight}


def parse_pnml(data: bytes) -> PTNet:
    root = ET.fromstring(data)
    places: list[str] = []
    initial: dict[str, int] = {}
    transitions: list[str] = []
    arcs: list[tuple[str, str, int]] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "place":
            pid = el.attrib["id"]
            places.append(pid)
            marking = next((c for c in el if _local(c.tag) == "initialMarking"), None)
            initial[pid] = _text_int(marking, 0)
        elif tag == "transition":
            transitions.append(el.attrib["id"])
        elif tag == "arc":
            if el.attrib.get("type", "normal") not in ("normal",):
                raise ValueError(f"unsupported arc type {el.attrib.get('type')!r}")
            inscription = next((c for c in el if _local(c.tag) == "inscription"), None)
            arcs.append((el.attrib["source"], el.attrib["target"], _text_int(inscription, 1)))
    net = PTNet(places=places, initial=initial, transitions=transitions)
    place_set, trans_set = set(places), set(transitions)
    for t in transitions:
        net.pre[t] = {}
        net.post[t] = {}
    for src, dst, w in arcs:
        if src in place_set and dst in trans_set:
            net.pre[dst][src] = net.pre[dst].get(src, 0) + w
        elif src in trans_set and dst in place_set:
            net.post[src][dst] = net.post[src].get(dst, 0) + w
        else:
            raise ValueError(f"arc {src} -> {dst} does not connect a place and a transition")
    return net


@dataclass
class Property:
    id: str
    formula: ET.Element  # the <formula> element


def parse_properties(data: bytes) -> list[Property]:
    root = ET.fromstring(data)
    props = []
    for p in root.iter():
        if _local(p.tag) != "property":
            continue
        pid = next(c.text.strip() for c in p if _local(c.tag) == "id")
        formula = next(c for c in p if _local(c.tag) == "formula")
        props.append(Property(id=pid, formula=formula))
    return props


class _FormulaBuilder:
    """Turn an MCC LTL formula into Spot syntax, collecting the atomic propositions."""

    def __init__(self, net: PTNet, var: dict[str, str], transition_index: dict[str, int]):
        self.net = net
        self.var = var
        self.transition_index = transition_index
        self.aps: dict[str, str] = {}          # ap name -> guard text
        self._atom_names: dict[str, str] = {}  # guard text -> ap name

    def _ap(self, prefix: str, guard: str) -> str:
        if guard not in self._atom_names:
            name = f"{prefix}{len(self._atom_names)}"
            self._atom_names[guard] = name
            self.aps[name] = guard
        return self._atom_names[guard]

    def _int_expr(self, el: ET.Element) -> tuple[str, int | None]:
        tag = _local(el.tag)
        if tag == "integer-constant":
            v = int(el.text.strip())
            return str(v), v
        if tag == "tokens-count":
            names = [self.var[c.text.strip()] for c in el if _local(c.tag) == "place"]
            if not names:
                return "0", 0
            return " + ".join(names), None
        raise ValueError(f"unsupported integer expression <{tag}>")

    def _enabled(self, t: str) -> str:
        pre = self.net.pre[t]
        if not pre:
            return "true"
        guard = " && ".join(f"{self.var[p]} >= {w}" for p, w in sorted(pre.items()))
        return self._ap("e", guard)

    def build(self, el: ET.Element) -> str:
        tag = _local(el.tag)
        kids = list(el)
        if tag in ("formula", "all-paths"):
            if len(kids) != 1:
                raise ValueError(f"<{tag}> must have exactly one child")
            return self.build(kids[0])
        if tag == "exists-path":
            raise ValueError("E path quantifier is not LTL")
        if tag == "negation":
            return f"!({self.build(kids[0])})"
        if tag == "conjunction":
            return "(" + " & ".join(self.build(k) for k in kids) + ")"
        if tag == "disjunction":
            return "(" + " | ".join(self.build(k) for k in kids) + ")"
        if tag == "globally":
            return f"G({self.build(kids[0])})"
        if tag == "finally":
            return f"F({self.build(kids[0])})"
        if tag == "next":
            return f"X({self.build(kids[0])})"
        if tag == "until":
            before = next(k for k in kids if _local(k.tag) == "before")
            reach = next(k for k in kids if _local(k.tag) == "reach")
            return f"(({self.build(list(before)[0])}) U ({self.build(list(reach)[0])}))"
        if tag == "true":
            return "true"
        if tag == "false":
            return "false"
        if tag == "integer-le":
            (ls, lv), (rs, rv) = (self._int_expr(k) for k in kids)
            if lv is not None and rv is not None:
                return "true" if lv <= rv else "false"
            return self._ap("a", f"{ls} <= {rs}")
        if tag == "is-fireable":
            ts = [c.text.strip() for c in kids if _local(c.tag) == "transition"]
            parts = [self._enabled(t) for t in ts]
            if "true" in parts:
                return "true"
            return "(" + " | ".join(parts) + ")" if parts else "false"
        raise ValueError(f"unsupported formula element <{tag}>")


class TooManyCubes(Exception):
    """The dead markings need more stutter cubes than allowed."""


def deadlock_cubes(net: PTNet, max_cubes: int = 64, timeout_ms: int = 60_000) -> list[list[tuple[str, int]]]:
    """Cubes [(place, w), ...] meaning AND(place <= w - 1) that cover every dead marking.

    Each cube disables every transition (it implies "dead" on its own), and together they cover
    all dead markings that satisfy the place invariants and non-negativity (an over-approximation
    of the reachable markings), so adding `[] cube -> skip` commands makes every reachable dead
    marking stutter and no reachable live marking. Integer semantics, via Z3."""
    import z3

    places = net.places
    x = {p: z3.Int(f"m_{i}") for i, p in enumerate(places)}
    inv = [x[p] >= 0 for p in places]
    # Place invariants of the net (left null space of the incidence matrix), over the integers.
    deltas = [[net.post[t].get(p, 0) - net.pre[t].get(p, 0) for p in places] for t in net.transitions]
    inv += _place_invariant_constraints(places, deltas, [net.initial[p] for p in places], x)

    def enabled(t):
        pre = net.pre[t]
        return z3.And([x[p] >= w for p, w in pre.items()]) if pre else z3.BoolVal(True)

    dead = z3.And([z3.Not(enabled(t)) for t in net.transitions])
    s = z3.Solver()
    s.set("timeout", timeout_ms)
    s.add(*inv)
    s.add(dead)
    cubes: list[list[tuple[str, int]]] = []
    while True:
        res = s.check()
        if res == z3.unknown:
            raise TooManyCubes("z3 timeout while enumerating dead markings")
        if res == z3.unsat:
            return cubes
        if len(cubes) >= max_cubes:
            raise TooManyCubes(f"more than {max_cubes} stutter cubes")
        m = s.model()
        val = {p: m.eval(x[p], model_completion=True).as_long() for p in places}
        # One disabling literal per transition, preferring places already in the cube.
        lits: dict[str, int] = {}
        for t in net.transitions:
            pre = net.pre[t]
            if any(p in lits and lits[p] <= w for p, w in pre.items()):
                continue  # already disabled by a literal p <= lits[p] - 1 <= w - 1
            p, w = min(((p, w) for p, w in pre.items() if val[p] < w), key=lambda pw: (pw[0] not in lits, pw[1]))
            lits[p] = min(lits.get(p, w), w)
        # Generalize: drop literals while the cube still implies "dead" under the invariants.
        for p in list(lits):
            trial = {q: w for q, w in lits.items() if q != p}
            g = z3.Solver()
            g.set("timeout", timeout_ms)
            g.add(*inv)
            g.add(*[x[q] <= w - 1 for q, w in trial.items()])
            g.add(z3.Not(dead))
            if g.check() == z3.unsat:
                lits = trial
        cube = sorted(lits.items(), key=lambda pw: places.index(pw[0]))
        cubes.append(cube)
        s.add(z3.Not(z3.And([x[p] <= w - 1 for p, w in cube]) if cube else z3.BoolVal(True)))


def _place_invariant_constraints(places, deltas, m0, x):
    """z3 constraints y·m == y·m0 for an integer basis of place invariants."""
    from fractions import Fraction
    from math import gcd, lcm
    import z3

    n = len(places)
    mat = [[Fraction(d[i]) for i in range(n)] for d in deltas]
    pivots, r = [], 0
    for col in range(n):
        piv = next((i for i in range(r, len(mat)) if mat[i][col] != 0), None)
        if piv is None:
            continue
        mat[r], mat[piv] = mat[piv], mat[r]
        pv = mat[r][col]
        mat[r] = [v / pv for v in mat[r]]
        for i in range(len(mat)):
            if i != r and mat[i][col] != 0:
                f = mat[i][col]
                mat[i] = [a - f * b for a, b in zip(mat[i], mat[r])]
        pivots.append(col)
        r += 1
        if r == len(mat):
            break
    out = []
    for fcol in (c for c in range(n) if c not in pivots):
        y = [Fraction(0)] * n
        y[fcol] = Fraction(1)
        for i, pcol in enumerate(pivots):
            y[pcol] = -mat[i][fcol]
        den = 1
        for f in y:
            den = lcm(den, f.denominator)
        yi = [int(f * den) for f in y]
        g = 0
        for c in yi:
            g = gcd(g, abs(c))
        yi = [c // g for c in yi] if g else yi
        out.append(z3.Sum([yi[i] * x[places[i]] for i in range(n) if yi[i]] or [z3.IntVal(0)])
                   == sum(yi[i] * m0[i] for i in range(n)))
    return out


def _sanitize(ident: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", ident)


def net_to_gc(net: PTNet, prop: Property | None = None, bound: int | None = None,
              name: str = "", stutter: list[list[tuple[str, int]]] | None = None) -> str:
    """Render the net (and optionally one LTL property) as `.gc` text.

    `stutter` (from deadlock_cubes) adds one stutter command per cube."""
    var = {p: f"p{i}" for i, p in enumerate(net.places)}
    out = [f"// MCC instance {name}" if name else "// MCC P/T net",
           f"// {len(net.places)} places, {len(net.transitions)} transitions"]
    out.append("// places: " + ", ".join(f"{var[p]}={_sanitize(p)}" for p in net.places))
    out.append("")
    if bound is not None:
        for p in net.places:
            out.append(f"type {var[p]}: 0..{bound}")
        out.append("")
    out.append("init: " + " && ".join(f"{var[p]} == {net.initial[p]}" for p in net.places))
    out.append("")
    for t in net.transitions:
        pre, post = net.pre[t], net.post[t]
        guard = " && ".join(f"{var[p]} >= {w}" for p, w in sorted(pre.items())) or "true"
        updates = []
        for p in net.places:
            delta = post.get(p, 0) - pre.get(p, 0)
            if delta > 0:
                updates.append(f"{var[p]} = {var[p]} + {delta}")
            elif delta < 0:
                updates.append(f"{var[p]} = {var[p]} - {-delta}")
        if not updates:  # only read arcs: the marking is unchanged
            p0 = next(iter(sorted(pre)), net.places[0])
            updates.append(f"{var[p0]} = {var[p0]}")
        out.append(f"// {_sanitize(t)}")
        out.append(f"[] {guard} -> " + "; ".join(updates))
    for cube in stutter or []:
        guard = " && ".join(f"{var[p]} <= {w - 1}" for p, w in cube) or "true"
        out.append("// deadlock: stutter (MCC semantics)")
        out.append(f"[] {guard} -> {var[net.places[0]]} = {var[net.places[0]]}")
    if prop is not None:
        builder = _FormulaBuilder(net, var, {t: i for i, t in enumerate(net.transitions)})
        spec = builder.build(prop.formula)
        out.append("")
        out.append(f"// {prop.id}")
        for ap_name, guard in builder.aps.items():
            out.append(f"ap {ap_name} := {guard}")
        out.append(f'spec: "{spec}"')
    return "\n".join(out) + "\n"


def read_instance(tgz: Path) -> dict[str, bytes]:
    """Return the member files of one MCC instance archive, keyed by basename."""
    files = {}
    with tarfile.open(tgz) as tf:
        for m in tf.getmembers():
            if m.isfile():
                fh = tf.extractfile(m)
                if fh is not None:
                    files[Path(m.name).name] = fh.read()
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("instance", type=Path, help="MCC instance archive (<name>.tgz)")
    ap.add_argument("--examination", default="LTLCardinality",
                    choices=["LTLCardinality", "LTLFireability"])
    ap.add_argument("--index", type=int, default=None, help="Property index (default: net only)")
    ap.add_argument("--bound", type=int, default=None, help="Add `type p: 0..BOUND` for every place")
    args = ap.parse_args(argv)

    files = read_instance(args.instance)
    net = parse_pnml(files["model.pnml"])
    prop = None
    if args.index is not None:
        prop = parse_properties(files[f"{args.examination}.xml"])[args.index]
    sys.stdout.write(net_to_gc(net, prop, args.bound, args.instance.name.removesuffix(".tgz")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
