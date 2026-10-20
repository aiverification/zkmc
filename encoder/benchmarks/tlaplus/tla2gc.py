"""Translate a finite TLA+ model (spec + TLC configuration) into a `.gc` program.

The variables are laid out as integer slots (`layout.py`, inferred from the states TLC reaches),
and the next-state relation is grounded into guarded commands by symbolic evaluation
(`tla_eval.py`): every branch of `Next` (disjunctions, existentials over the model's finite
constants, IF/CASE) becomes one or more commands whose guards are conjunctions of linear
comparisons over slots. The commands agree with the TLA+ next-state relation on every state
of the layout's domain, so the `.gc` program and the TLA+ model have the same reachable states
and transitions; `run_tlaplus.py --check-states` verifies this against TLC.

TLA+ behaviours may stutter at any time, so the program gets an unconditional stutter command.

Properties:
- every `INVARIANT` p becomes the LTL property `G p`;
- every temporal `PROPERTY` becomes LTL over state predicates, with the specification's weak
  and strong fairness conditions as assumptions (`fairness => property`); an auxiliary slot
  `act` then records which fairness-relevant action was taken last.
State predicates are compiled to DNF over the slots; each cube becomes one atomic proposition
(`ap`), and the LTL formula is put in negation normal form first so that every predicate
occurs positively (the negation of the property is what Spot translates).
"""

from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from layout import Layout, Names, infer, share_tables  # noqa: E402
from tla_eval import Branch, Ctx, Env, Evaluator, Thunk  # noqa: E402
from tla_front import Cfg, Loader, named, parse_cfg, text  # noqa: E402
from tla_sym import (FALSE, TRUE, Cube, Lin, Ranges, TooBig, _merge, atom, conj, disj, neg,  # noqa: E402
                     render_cube, to_dnf)
from tla_values import EvalError, show  # noqa: E402

COMMAND_LIMIT = 20000


class TranslationError(Exception):
    """The model is outside the supported fragment (message says why)."""


@dataclass
class Command:
    cube: Cube
    assign: dict          # slot -> Lin (identities removed)
    label: str


@dataclass
class Fairness:
    kind: str             # 'WF' | 'SF'
    node: object          # the action
    env: Env
    sub: object           # subscript node
    label: str


@dataclass
class Translation:
    model: str
    variables: list
    layouts: dict
    slots: list
    ranges: Ranges
    init: list            # init comparisons (text)
    init_states: int
    commands: list
    fairness: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    ev: Evaluator | None = None
    names: Names | None = None
    boot_slot: str | None = None   # set if the initial states need a boot step
    boot_states: list | None = None

    # ------------------------------------------------------------------ rendering

    def render(self, prop: "PropertyGC | None" = None) -> str:
        out = [f"// TLA+ model {self.model}",
               f"// {len(self.variables)} variables, {len(self.slots)} slots, "
               f"{len(self.commands)} commands, {self.init_states} initial states"]
        for v in self.variables:
            out.append(f"//   {v}: {' '.join(self.layouts[v].slots()) or '(constant)'}")
        for d in (d for v in self.variables for d in self.layouts[v].describe()):
            out.append(f"//   {d}")
        for n in self.notes:
            out.append(f"// note: {n}")
        out.append("")
        extra = dict(prop.extra_slots) if prop else {}
        if self.boot_slot:
            extra[self.boot_slot] = (0, 1)
        commands = prop.commands if prop and prop.commands is not None else self.commands
        for s in self.slots:
            lo, hi = self.ranges.get(s)
            out.append(f"type {s}: {lo}..{hi}")
        for s, (lo, hi) in extra.items():
            out.append(f"type {s}: {lo}..{hi}")
        out.append("")
        init = list(self.init) + [f"{s} == 0" for s in extra]
        if self.boot_slot:
            init += [f"{s} == {self.ranges.get(s)[0]}" for s in self.slots]
        out.append("init: " + (" && ".join(init) if init else "true"))
        out.append("")
        guard0 = f"{self.boot_slot} == 1 && " if self.boot_slot else ""
        if self.boot_slot:
            for k, st in enumerate(self.boot_states):
                assign = {s: Lin({}, v) for s, v in zip(self.slots, st) if v != self.ranges.get(s)[0]}
                assign[self.boot_slot] = Lin({}, 1)
                out.append(f"// initial state {k + 1}")
                out.append(f"[] {self.boot_slot} == 0 -> " + _render_assign(assign, self.slots))
        for i, c in enumerate(commands):
            assign = dict(c.assign)
            if prop and prop.act_of is not None:
                assign[prop.act_slot] = Lin({}, prop.act_of[i])
            out.append(f"// {c.label}")
            guard = render_cube(c.cube, self.ranges)
            out.append(f"[] {guard0 + guard if guard != 'true' or not guard0 else guard0[:-4]} -> "
                       + _render_assign(assign, self.slots))
        stutter = f"{prop.act_slot} = 0" if prop and prop.act_slot else f"{self.slots[0]} = {self.slots[0]}"
        out.append("// stuttering")
        out.append(f"[] {guard0[:-4] if guard0 else 'true'} -> {stutter}")
        if prop:
            ranges = self.ranges_with(extra)
            out.append("")
            out.append(f"// {prop.name}: {prop.description}")
            out.append(f"// LTL: {prop.ltl}")
            for name, f in prop.preds.items():
                text_ = " || ".join(f"({render_cube(c, ranges)})" for c in to_dnf(f, ranges))
                out.append(f"//   {name} := {text_[:300]}{' ...' if len(text_) > 300 else ''}")
            out.append("// Büchi automaton of the negated property (Spot ltl2tgba -B)")
            out.append("automaton_init: " + ", ".join(prop.init_states))
            for src, dst, cube, fair in prop.transitions:
                out.append(f"trans{'!' if fair else ''}({src}, {dst}): {render_cube(cube, ranges)}")
        return "\n".join(out) + "\n"

    def ranges_with(self, extra: dict) -> Ranges:
        if not extra:
            return self.ranges
        return Ranges({**self.ranges.r, **extra})


def _render_assign(assign: dict, slots: list) -> str:
    order = {s: i for i, s in enumerate(slots)}
    parts = [f"{s} = {lin.render()}" for s, lin in sorted(assign.items(), key=lambda kv: order.get(kv[0], -1))]
    return "; ".join(parts)


# ----------------------------------------------------------------------------------------------
# Specification structure
# ----------------------------------------------------------------------------------------------

def _resolve_def(ev: Evaluator, name: str):
    kind, *rest = ev.resolve(name, ev.scope.env)
    if kind != "def":
        raise TranslationError(f"{name} is not a definition")
    d, scope = rest
    return d, scope


def decompose_spec(ev: Evaluator, node, env: Env, init: list, nxt: list, fair: list, label=""):
    """Split `Init /\\ [][Next]_v /\\ WF_v(A) /\\ ...` into its parts."""
    t = node.type
    if t == "parentheses":
        return decompose_spec(ev, named(node)[0], env, init, nxt, fair, label)
    if t == "conj_list":
        for item in named(node):
            decompose_spec(ev, named(item)[-1], env, init, nxt, fair, label)
        return
    if t == "bound_infix_op" and node.child_by_field_name("symbol").type == "land":
        decompose_spec(ev, node.child_by_field_name("lhs"), env, init, nxt, fair, label)
        decompose_spec(ev, node.child_by_field_name("rhs"), env, init, nxt, fair, label)
        return
    if t == "bound_prefix_op" and node.child_by_field_name("symbol").type == "always":
        rhs = node.child_by_field_name("rhs")
        while rhs.type == "parentheses":
            rhs = named(rhs)[0]
        if rhs.type == "step_expr_or_stutter":
            nxt.append((named(rhs)[0], env))
            return
        raise TranslationError(f"unsupported temporal conjunct in the specification: {text(node)[:60]}")
    if t == "fairness":
        kids = named(node)
        kind = "WF" if text(node).startswith("WF_") else "SF"
        fair.append(Fairness(kind, kids[-1], env, kids[0], label or text(kids[-1])[:60]))
        return
    if t == "bounded_quantification" and node.child_by_field_name("quantifier").type == "forall":
        bounds = [c for c in node.named_children if c.type == "quantifier_bound"]
        for c, e2 in ev.enumerate_bindings(bounds, env, Ctx()):
            if c is not TRUE:
                raise TranslationError("fairness quantified over a state-dependent set")
            args = ", ".join(show(v) for v in e2.names.values() if not isinstance(v, Thunk))
            decompose_spec(ev, named(node)[-1], e2, init, nxt, fair, f"{text(named(node)[-1])[:40]} [{args}]")
        return
    if t in ("identifier_ref", "bound_op"):
        name = text(node) if t == "identifier_ref" else text(node.child_by_field_name("name"))
        kind, *rest = ev.resolve(name, env)
        if kind == "def" and rest[0].kind == "op":
            d, scope = rest
            args = [] if t == "identifier_ref" else [Thunk(c, env, Ctx()) for c in ev._op_args(node)]
            if _is_temporal(ev, d.body, Env({}, scope.env, scope)):
                names_d = {(p if isinstance(p, str) else p[0]): a for p, a in zip(d.params, args)}
                return decompose_spec(ev, d.body, Env(names_d, scope.env, scope), init, nxt, fair, label)
    init.append((node, env))


def _is_temporal(ev: Evaluator, node, env, depth=0) -> bool:
    """Does the expression contain temporal operators ([] <> ~> WF SF [A]_v), looking through
    operator definitions?"""
    if depth > 30:
        return False
    stack = [node]
    while stack:
        n = stack.pop()
        t = n.type
        if t in ("fairness", "step_expr_or_stutter", "leads_to", "always", "eventually",
                 "temporal_forall", "temporal_exists"):
            return True
        if t == "identifier_ref" or (t == "bound_op"):
            name = text(n) if t == "identifier_ref" else text(n.child_by_field_name("name"))
            try:
                kind, *rest = ev.resolve(name, env)
            except EvalError:
                kind = None
            if kind == "def" and rest[0].kind == "op" and rest[0].body is not None:
                key = ("temporal", id(rest[0]))
                hit = ev._cache.get(key)
                if hit is None:
                    ev._cache[key] = (False,)  # recursion guard
                    r = _is_temporal(ev, rest[0].body, rest[1].env, depth + 1)
                    ev._cache[key] = (r,)
                    hit = (r,)
                if hit[0]:
                    return True
        stack.extend(n.named_children)
    return False


# ----------------------------------------------------------------------------------------------
# Translation
# ----------------------------------------------------------------------------------------------

def build(spec_dir: Path, module: str, cfg: Cfg, states: list, model_name: str = "") -> Translation:
    loader = Loader(spec_dir)
    try:
        root = loader.load(module)
    except EvalError as e:
        raise TranslationError(str(e))
    variables = list(root.variables)
    if not variables:
        raise TranslationError("no variables")
    if not states:
        raise TranslationError("no reachable states")
    names = Names()
    layouts: dict[str, Layout] = {}
    for v in variables:
        layouts[v] = infer(v, [s[v] for s in states], names)
    share_tables(list(layouts.values()))
    slots = [s for v in variables for s in layouts[v].slots()]
    ranges = {}
    slot_values = {}
    for v in variables:
        ranges.update(layouts[v].ranges())
        slot_values.update(layouts[v].values())
    for v in variables:  # unused sequence positions hold defaults outside the observed values
        for s, d in layouts[v].default().items():
            lo, hi = ranges[s]
            ranges[s] = (min(lo, d), max(hi, d))
            if d not in slot_values[s]:
                slot_values[s] = sorted(set(slot_values[s]) | {d})
    if not slots:
        raise TranslationError("the model has a single state")
    ev = Evaluator(loader, root, dict(cfg.constants), dict(cfg.overrides),
                   cur={v: layouts[v].read() for v in variables}, slot_values=slot_values)
    tr = Translation(model_name, variables, layouts, slots, Ranges(ranges), [], 0, [], ev=ev, names=names)

    init_parts, next_parts, fair = [], [], []
    if cfg.spec:
        d, scope = _resolve_def(ev, cfg.spec)
        decompose_spec(ev, d.body, scope.env, init_parts, next_parts, fair)
    else:
        if not cfg.init or not cfg.next:
            raise TranslationError("cfg has neither SPECIFICATION nor INIT/NEXT")
        d, scope = _resolve_def(ev, cfg.init)
        init_parts.append((d.body, scope.env))
        d, scope = _resolve_def(ev, cfg.next)
        next_parts.append((d.body, scope.env))
    if len(next_parts) != 1:
        raise TranslationError(f"expected one [][Next]_v conjunct, found {len(next_parts)}")
    tr.fairness = fair

    try:
        _translate_init(tr, init_parts)
        _translate_next(tr, next_parts[0], cfg)
    except (TooBig, RecursionError) as e:
        raise TranslationError(f"too large: {e}")
    except EvalError as e:
        raise TranslationError(f"{type(e).__name__}: {e}")
    return tr


def _translate_init(tr: Translation, parts):
    ev = tr.ev
    branches = [Branch(TRUE, {})]
    for node, env in parts:
        branches = ev.ev_action(node, env, branches, Ctx(init=True))
    states = set()
    for br in branches:
        if br.guard is not TRUE:
            raise TranslationError("Init depends on a symbolic condition")
        missing = [v for v in tr.variables if v not in br.assigned]
        if missing:
            raise TranslationError(f"Init does not assign {', '.join(missing)}")
        per_var = []
        for v in tr.variables:
            alts = ev.concretize(ev.force(br.assigned[v]))
            if any(c is not TRUE and len(alts) == 1 for c, _ in alts):
                raise TranslationError("Init value depends on a condition")
            encs = []
            for _, x in alts:
                e = tr.layouts[v].encode(x)
                if e is None:
                    raise TranslationError(f"initial value of {v} not among the reachable values: {show(x)}")
                encs.append(e)
            per_var.append(encs)
        for combo in itertools.product(*per_var):
            st = {}
            for e in combo:
                st.update(e)
            states.add(tuple(st[s] for s in tr.slots))
    if not states:
        raise TranslationError("no initial state")
    tr.init_states = len(states)
    try:
        tr.init = init_constraints(tr.slots, states, tr.ranges)
    except TranslationError:
        if len(states) > 5000:
            raise
        # Not a polyhedron: start in a `boot` state whose only successors are the initial
        # states (one command each); properties are then evaluated from the second state.
        tr.boot_slot = tr.names.new("boot")
        tr.boot_states = sorted(states)


def init_constraints(slots: list, states: set, ranges: Ranges) -> list[str]:
    """A conjunction of linear constraints whose integer points are exactly `states`: the
    bounding box, the affine hull, and bounds on sums/differences of pairs of slots (and on
    the sum of all varying slots). Raises TranslationError if these do not describe the set."""
    from fractions import Fraction

    pts = sorted(states)
    cols = list(zip(*pts))
    lo = [min(c) for c in cols]
    hi = [max(c) for c in cols]
    out = []
    for s, a, b in zip(slots, lo, hi):
        if a == b:
            out.append(f"{s} == {a}")
        else:
            out.append(f"{s} >= {a}")
            out.append(f"{s} <= {b}")
    box = 1
    for a, b in zip(lo, hi):
        box *= b - a + 1
    if box == len(pts):
        return out
    var = [i for i in range(len(slots)) if lo[i] < hi[i]]
    # Affine hull: integer (a, c) with a.x = c on every point (null space of [x | 1]).
    rows = [[Fraction(p[i]) for i in var] + [Fraction(1)] for p in pts]
    eqs = _nullspace(rows, len(var) + 1)
    cons = []  # (coeffs over var indices, op, const)  meaning sum(coeffs * x) op const
    for y in eqs:
        coeffs = {var[j]: y[j] for j in range(len(var)) if y[j]}
        if coeffs:
            cons.append((coeffs, "==", -y[-1]))
    # Octagon-style bounds and the total.
    dirs = []
    for a, b in itertools.combinations(var, 2):
        dirs += [{a: 1, b: 1}, {a: 1, b: -1}, {a: -1, b: 1}, {a: -1, b: -1}]
    if len(var) > 2:
        dirs += [{i: 1 for i in var}, {i: -1 for i in var}]
    for d in dirs:
        m = max(sum(c * p[i] for i, c in d.items()) for p in pts)
        if m < sum(c * (hi[i] if c > 0 else lo[i]) for i, c in d.items()):  # cuts the box
            cons.append((d, "<=", m))
    if not _exact(pts, lo, hi, cons):
        raise TranslationError(f"{len(pts)} initial states are not the integer points of a polyhedron "
                               f"(box size {box})")
    for d, op, c in cons:
        lhs = " + ".join((f"{k}*{slots[i]}" if k not in (1, -1) else ("" if k == 1 else "-") + slots[i])
                         for i, k in sorted(d.items())).replace("+ -", "- ")
        out.append(f"{lhs} {op} {c}")
    return out


def _nullspace(rows, n):
    """Integer basis of {y : row . y = 0 for every row} (rows of Fractions, length n)."""
    from fractions import Fraction
    from math import gcd, lcm

    mat = [list(r) for r in rows]
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
    basis = []
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
        basis.append([c // g for c in yi] if g else yi)
    return basis


def _exact(pts, lo, hi, cons) -> bool:
    """Are `pts` all the integer points of the box that satisfy `cons`?"""
    import z3

    n = len(lo)
    x = [z3.Int(f"x{i}") for i in range(n)]
    s = z3.Solver()
    s.set("timeout", 30000)
    for i in range(n):
        s.add(x[i] >= lo[i], x[i] <= hi[i])
    for d, op, c in cons:
        e = z3.Sum([k * x[i] for i, k in d.items()])
        s.add(e == c if op == "==" else e <= c)
    if len(pts) > 20000:
        return False
    # Every point satisfies the constraints by construction; look for one more.
    s.add(z3.Not(z3.Or([z3.And([x[i] == p[i] for i in range(n)]) for p in pts])))
    return s.check() == z3.unsat


def _translate_next(tr: Translation, part, cfg: Cfg):
    ev = tr.ev
    node, env = part
    branches = ev.ev_action(node, env, [Branch(TRUE, {})], Ctx())
    # TLC discards successors that violate a state CONSTRAINT or an ACTION_CONSTRAINT.
    constraints = [_resolve_def(ev, n) for n in cfg.constraints]
    action_constraints = [_resolve_def(ev, n) for n in cfg.action_constraints]
    by_assign: dict = {}
    for br in branches:
        missing = [v for v in tr.variables if v not in br.assigned]
        if missing:
            raise TranslationError(f"{', '.join(m + chr(39) for m in missing)} not assigned "
                                   f"in a branch of {br.label or 'Next'}")
        guard = br.guard
        for d, scope in constraints:  # on the successor state
            guard = conj(guard, ev.to_formula(ev.ev(d.body, scope.env, Ctx(br.assigned, primed=True))))
        for d, scope in action_constraints:
            guard = conj(guard, ev.to_formula(ev.ev(d.body, scope.env, Ctx(br.assigned))))
        if guard is FALSE:
            continue
        alts = [(guard, {})]
        for v in tr.variables:
            flat = tr.layouts[v].flatten(ev.force(br.assigned[v]), ev)
            nxt = []
            for c, a in alts:
                for c2, a2 in flat:
                    cc = conj(c, c2)
                    if cc is not FALSE:
                        nxt.append((cc, {**a, **a2}))
            alts = nxt
            if len(alts) > COMMAND_LIMIT:
                raise TooBig("too many command alternatives")
            if not alts:
                break
        for c, assign in alts:
            for cube in to_dnf(c, tr.ranges):
                # Drop assignments that keep the slot's value (`x = x`, or `x = k` where the
                # guard fixes x == k); a command that changes nothing is a stuttering step,
                # covered by the stutter command.
                a = {s: lin for s, lin in assign.items()
                     if lin != Lin.var(s) and not (lin.is_const() and cube.bounds.get(s) == (lin.const, lin.const))}
                if not a:
                    continue
                key = tuple(sorted((s, lin.key()) for s, lin in a.items()))
                entry = by_assign.setdefault(key, (a, [], []))
                entry[1].append(cube)
                if br.label and br.label not in entry[2]:
                    entry[2].append(br.label)
    commands = []
    for assign, cubes, labels in by_assign.values():
        for cube in _merge(cubes, tr.ranges):
            commands.append(Command(cube, assign, ", ".join(labels[:3]) + (" ..." if len(labels) > 3 else "")))
    if len(commands) > COMMAND_LIMIT:
        raise TooBig(f"{len(commands)} commands")
    tr.commands = commands


# ----------------------------------------------------------------------------------------------
# Properties
# ----------------------------------------------------------------------------------------------

@dataclass
class PropertyGC:
    name: str
    kind: str              # 'invariant' | 'temporal'
    description: str
    preds: dict            # predicate name -> Formula (state predicates of the LTL formula)
    ltl: str               # the property, in Spot syntax over the predicates
    transitions: list = field(default_factory=list)  # (src, dst, Cube, fair): Büchi automaton of !ltl
    init_states: list = field(default_factory=list)
    extra_slots: dict = field(default_factory=dict)   # e.g. act -> (0, k)
    commands: list | None = None                     # replaces the model's commands
    act_of: list | None = None                       # per command: value of act
    act_slot: str | None = None


class Tautology(Exception):
    """The property holds on every state of the layout (nothing to prove)."""


class _APs:
    """Named state predicates of an LTL formula."""

    def __init__(self, ranges: Ranges):
        self.ranges = ranges
        self.preds: dict = {}
        self._by_formula: dict = {}

    def predicate(self, f, positive: bool = True) -> str:
        """Spot text for a state formula (or its negation)."""
        g = f if positive else neg(f)
        if g is TRUE or g is FALSE:
            return "true" if g is TRUE else "false"
        cubes = to_dnf(g, self.ranges)
        if not cubes:
            return "false"
        if any(not c.bounds and not c.atoms for c in cubes):
            return "true"
        name = self._by_formula.get(g)
        if name is None:
            name = self._by_formula[g] = f"p{len(self.preds)}"
            self.preds[name] = g
        return name


def lower(prop: PropertyGC, ranges: Ranges, boot: bool = False):
    """Spot's Büchi automaton for !ltl, with each edge label turned into guards: a label cube
    over the predicates becomes the DNF (on the slots' ranges) of their conjunction. With a boot
    step, the property applies from the second state on (`X ltl`)."""
    from zkterm_tool.ltl import parse_hoa, run_ltl2tgba

    if boot:
        prop.ltl = f"X({prop.ltl})"
    import os
    # The Spot wrapper stops ltl2tgba after LTL2TGBA_TIMEOUT seconds itself; wait a bit longer.
    aut = parse_hoa(run_ltl2tgba(prop.ltl, timeout=float(os.environ.get("LTL2TGBA_TIMEOUT", 600)) + 60))
    index = {i: prop.preds[n] for i, n in enumerate(aut.ap_names)}
    trans = []
    for e in aut.edges:
        fair = aut.all_accepting or e.src in aut.accepting_states or e.edge_accepting
        for cube in e.dnf:
            f = conj(*(index[i] if pol else neg(index[i]) for i, pol in cube.items()))
            for c in to_dnf(f, ranges):
                trans.append((f"q{e.src}", f"q{e.dst}", c, fair))
    if not trans:
        raise Tautology(prop.name)  # the negated property has no models
    prop.transitions = trans
    prop.init_states = [f"q{q}" for q in aut.start_states]


def invariant_property(tr: Translation, name: str) -> PropertyGC:
    ev = tr.ev
    d, scope = _resolve_def(ev, name)
    try:
        p = ev.to_formula(ev.ev(d.body, scope.env, Ctx()))
        if not to_dnf(neg(p), tr.ranges):
            raise Tautology(name)
        aps = _APs(tr.ranges)
        good = aps.predicate(p)
        prop = PropertyGC(name, "invariant", f"invariant {name}", aps.preds, f"G({good})")
        lower(prop, tr.ranges, bool(tr.boot_slot))
    except (TooBig, RecursionError) as e:
        raise TranslationError(f"too large: {e}")
    except EvalError as e:
        raise TranslationError(f"{type(e).__name__}: {e}")
    return prop


@dataclass
class FairnessInfo:
    act_slot: str
    codes: int                # act ranges over 0..codes
    commands: list            # the model's commands, split by which fairness actions they take
    act_of: list
    enabled: list             # per fairness condition: ENABLED <<A>>_v as a formula
    taken: list               # per fairness condition: act values of <<A>>_v steps


def fairness_info(tr: Translation, actions: list) -> FairnessInfo:
    """Split the commands by the given actions (the specification's fairness conditions, and
    actions a property refers to), and number the combinations of actions a step takes."""
    key = tuple(id(a) for a in actions)
    cache = tr.__dict__.setdefault("_fairness_infos", {})
    if key in cache:
        return cache[key]
    from layout import read_after
    from tla_sym import cube_formula

    ev = tr.ev
    regions = [(c.cube, c, frozenset()) for c in tr.commands]
    enabled = []
    for i, F in enumerate(actions):
        en = FALSE
        sub_vars = ev.unchanged_vars(F.sub, F.env)
        for br in ev.ev_action(F.node, F.env, [Branch(TRUE, {})], Ctx()):
            if sub_vars is not None and any(v not in br.assigned for v in sub_vars):
                en = disj(en, br.guard)  # an unconstrained x' can always change
                continue
            en = disj(en, conj(br.guard, neg(ev.unchanged_formula(F.sub, F.env, Ctx(br.assigned)))))
        enabled.append(en)
        nxt = []
        for cube, c, sig in regions:
            post = {v: read_after(tr.layouts[v], c.assign) for v in tr.variables}
            region = cube_formula(cube)
            taken = FALSE
            for b in ev.ev_action(F.node, F.env, [Branch(region, post)], Ctx()):
                taken = disj(taken, conj(b.guard, neg(ev.unchanged_formula(F.sub, F.env, Ctx(b.assigned)))))
            nxt += [(y, c, sig | {i}) for y in to_dnf(conj(region, taken), tr.ranges)]
            nxt += [(n, c, sig) for n in to_dnf(conj(region, neg(taken)), tr.ranges)]
        regions = nxt
        if len(regions) > COMMAND_LIMIT:
            raise TooBig("too many commands after splitting by fairness")
    sigs = sorted({sig for _, _, sig in regions if sig}, key=lambda g: sorted(g))
    code = {sig: k + 1 for k, sig in enumerate(sigs)}
    if getattr(tr, "_act_slot", None) is None:
        tr._act_slot = tr.names.new("act")
    info = FairnessInfo(tr._act_slot, len(sigs),
                        [Command(cube, c.assign, c.label) for cube, c, _ in regions],
                        [code.get(sig, 0) for _, _, sig in regions], enabled,
                        [[code[g] for g in sigs if i in g] for i in range(len(actions))])
    if all(a in tr.fairness for a in actions):  # property actions are rebuilt per property
        cache[key] = info
    return info


class _LTL:
    """Spot LTL for a TLA+ temporal formula, in negation normal form (state predicates become
    disjunctions of cube APs, each occurring positively)."""

    def __init__(self, tr: Translation, aps: "_APs"):
        self.tr, self.ev, self.aps = tr, tr.ev, aps
        self.actions = []  # actions of <<A>>_v and WF/SF in the property (see fill)

    def _action(self, node, env, sub, label) -> int:
        self.actions.append(Fairness("", node, env, sub, label))
        return len(self.actions) - 1

    def build(self, node, env, pos: bool) -> str:
        ev = self.ev
        t = node.type
        if t in ("parentheses", "label"):
            return self.build(named(node)[-1], env, pos)
        if t in ("conj_list", "disj_list"):
            parts = [self.build(named(item)[-1], env, pos) for item in named(node)]
            return self._join(parts, "&" if (t == "conj_list") == pos else "|")
        if t == "boolean":
            return "true" if (text(node) == "TRUE") == pos else "false"
        if t == "bound_prefix_op":
            st = node.child_by_field_name("symbol").type
            rhs = node.child_by_field_name("rhs")
            if st == "lnot":
                return self.build(rhs, env, not pos)
            if st == "always":
                if rhs.type == "step_expr_or_stutter" or (rhs.type == "parentheses" and named(rhs)[0].type == "step_expr_or_stutter"):
                    raise TranslationError("action property [][A]_v")
                return f"{'G' if pos else 'F'}({self.build(rhs, env, pos)})"
            if st == "eventually":
                return f"{'F' if pos else 'G'}({self.build(rhs, env, pos)})"
        if t == "bound_infix_op":
            st = node.child_by_field_name("symbol").type
            lhs, rhs = node.child_by_field_name("lhs"), node.child_by_field_name("rhs")
            if st in ("land", "lor"):
                return self._join([self.build(lhs, env, pos), self.build(rhs, env, pos)],
                                  "&" if (st == "land") == pos else "|")
            if st == "implies":
                if pos:
                    return self._join([self.build(lhs, env, False), self.build(rhs, env, True)], "|")
                return self._join([self.build(lhs, env, True), self.build(rhs, env, False)], "&")
            if st in ("iff", "equiv"):
                a, na = self.build(lhs, env, True), self.build(lhs, env, False)
                b, nb = self.build(rhs, env, True), self.build(rhs, env, False)
                if pos:
                    return f"(({a} & {b}) | ({na} & {nb}))"
                return f"(({a} & {nb}) | ({na} & {b}))"
            if st == "leads_to":
                if pos:  # [](P => <>Q)
                    return f"G({self.build(lhs, env, False)} | F({self.build(rhs, env, True)}))"
                return f"F({self.build(lhs, env, True)} & G({self.build(rhs, env, False)}))"
        if t == "bounded_quantification" and _is_temporal(ev, node, env):
            q = node.child_by_field_name("quantifier").type
            bounds = [c for c in node.named_children if c.type == "quantifier_bound"]
            parts = []
            for c, e2 in ev.enumerate_bindings(bounds, env, Ctx()):
                if c is not TRUE:
                    raise TranslationError("temporal quantifier over a state-dependent set")
                parts.append(self.build(named(node)[-1], e2, pos))
            return self._join(parts, "&" if (q == "forall") == pos else "|")
        if t == "if_then_else" and _is_temporal(ev, node, env):
            c = ev.to_formula(ev.ev(node.child_by_field_name("if"), env, Ctx()))
            then = self.build(node.child_by_field_name("then"), env, pos)
            else_ = self.build(node.child_by_field_name("else"), env, pos)
            return self._join([self._join([self.aps.predicate(c, True), then], "&"),
                               self._join([self.aps.predicate(c, False), else_], "&")], "|")
        if t == "step_expr_no_stutter":  # <<A>>_v: the first step is an A step changing v
            kids = [c for c in named(node) if c.type not in ("langle_bracket", "rangle_bracket_sub")]
            k = self._action(kids[0], env, kids[-1], text(kids[0])[:40])
            return f"X(@{'A' if pos else 'N'}{k}@)"
        if t == "fairness":
            kids = named(node)
            k = self._action(kids[-1], env, kids[0], text(kids[-1])[:40])
            wf = text(node).startswith("WF_")
            if pos:  # WF: GF !en | GF taken;  SF: FG !en | GF taken
                return f"({'G(F' if wf else 'F(G'}(@D{k}@)) | G(F(@A{k}@)))"
            return f"({'F(G' if wf else 'G(F'}(@E{k}@)) & F(G(@N{k}@)))"
        if t in ("step_expr_or_stutter", "temporal_forall", "temporal_exists"):
            raise TranslationError(f"unsupported temporal construct {t}")
        if t in ("identifier_ref", "bound_op") and _is_temporal(ev, node, env):
            name = text(node) if t == "identifier_ref" else text(node.child_by_field_name("name"))
            kind, *rest = ev.resolve(name, env)
            if kind == "def" and rest[0].kind == "op":
                d, scope = rest
                args = [] if t == "identifier_ref" else [Thunk(c, env, Ctx()) for c in ev._op_args(node)]
                names_d = {(p if isinstance(p, str) else p[0]): a for p, a in zip(d.params, args)}
                return self.build(d.body, Env(names_d, scope.env, scope), pos)
            if kind == "local" and isinstance(rest[0], Thunk):
                return self.build(rest[0].node, rest[0].env, pos)
        if _is_temporal(ev, node, env):
            raise TranslationError(f"unsupported temporal formula {text(node)[:60]!r}")
        f = ev.to_formula(ev.ev(node, env, Ctx()))
        return self.aps.predicate(f, pos)

    @staticmethod
    def _join(parts, op):
        unit, zero = ("true", "false") if op == "&" else ("false", "true")
        if zero in parts:
            return zero
        parts = [p for p in parts if p != unit]
        if not parts:
            return unit
        return parts[0] if len(parts) == 1 else "(" + f" {op} ".join(parts) + ")"


def _conjuncts(ev: Evaluator, node, env, label=""):
    """Top-level conjuncts of a temporal formula (through definitions and \\A over constant
    sets), as (label, node, env)."""
    t = node.type
    if t in ("parentheses", "label"):
        return _conjuncts(ev, named(node)[-1], env, label)
    if t == "conj_list":
        return [c for item in named(node) for c in _conjuncts(ev, named(item)[-1], env, label)]
    if t == "bound_infix_op" and node.child_by_field_name("symbol").type == "land":
        return (_conjuncts(ev, node.child_by_field_name("lhs"), env, label)
                + _conjuncts(ev, node.child_by_field_name("rhs"), env, label))
    if (t == "bounded_quantification" and node.child_by_field_name("quantifier").type == "forall"
            and _is_temporal(ev, node, env)):
        bounds = [c for c in node.named_children if c.type == "quantifier_bound"]
        out = []
        for c, e2 in ev.enumerate_bindings(bounds, env, Ctx()):
            if c is not TRUE:
                raise TranslationError("temporal quantifier over a state-dependent set")
            b = ",".join(f"{k}={show(v)}" for k, v in e2.names.items())
            out += _conjuncts(ev, named(node)[-1], e2, f"{label},{b}" if label else b)
        return out
    if t in ("identifier_ref", "bound_op") and _is_temporal(ev, node, env):
        name = text(node) if t == "identifier_ref" else text(node.child_by_field_name("name"))
        kind, *rest = ev.resolve(name, env)
        if kind == "def" and rest[0].kind == "op":
            d, scope = rest
            args = [] if t == "identifier_ref" else [Thunk(c, env, Ctx()) for c in ev._op_args(node)]
            names_d = {(p if isinstance(p, str) else p[0]): a for p, a in zip(d.params, args)}
            return _conjuncts(ev, d.body, Env(names_d, scope.env, scope), label)
    return [(label, node, env)]


def _instance(tr: Translation, pname: str, node, env, fairness: list, info_fairness: list):
    """One property instance (a top-level conjunct) assuming `fairness` (a subset of the spec's
    conditions); `act` tracks the actions of `info_fairness` (a superset) and of the property."""
    aps = _APs(tr.ranges)
    builder = _LTL(tr, aps)
    neg_p = builder.build(node, env, False)
    if neg_p == "false":
        return None
    actions = list(info_fairness) + builder.actions
    info = fairness_info(tr, actions) if actions else None
    ranges = tr.ranges_with({info.act_slot: (0, info.codes)}) if info else tr.ranges
    aps.ranges = ranges
    fair = []
    if info:
        act = Lin.var(info.act_slot)
        taken = [disj(*(atom(act - c, "==") for c in info.taken[i])) for i in range(len(actions))]
        for i, F in enumerate(info_fairness):
            if F not in fairness:
                continue
            nen = aps.predicate(info.enabled[i], positive=False)
            left = f"G(F({nen}))" if F.kind == "WF" else f"F(G({nen}))"
            fair.append(_LTL._join([left, f"G(F({aps.predicate(taken[i])}))"], "|"))
        m = len(info_fairness)
        for j in range(len(builder.actions)):
            i = m + j
            neg_p = (neg_p.replace(f"@A{j}@", aps.predicate(taken[i]))
                     .replace(f"@N{j}@", aps.predicate(taken[i], positive=False))
                     .replace(f"@E{j}@", aps.predicate(info.enabled[i]))
                     .replace(f"@D{j}@", aps.predicate(info.enabled[i], positive=False)))
    spec = _LTL._join(fair + [neg_p], "&")
    if spec == "false":
        return None
    desc = f"property {pname}"
    if tr.fairness:
        desc += f" under {len(fairness)} of the {len(tr.fairness)} fairness conditions"
    prop = PropertyGC(pname, "temporal", desc, aps.preds, f"!({spec})")
    try:
        lower(prop, ranges, bool(tr.boot_slot))
    except Tautology:
        return None
    if info and (fair or builder.actions):
        prop.extra_slots = {info.act_slot: (0, info.codes)}
        prop.commands, prop.act_of, prop.act_slot = info.commands, info.act_of, info.act_slot
    prop.fairness_used = len(fairness)
    return prop


def temporal_property(tr: Translation, name: str, holds=None) -> list:
    """`PROPERTY name` under the specification's fairness, one instance per top-level conjunct:
    Spot gets `!(fair & !P_i)`. Raises Tautology if every conjunct is trivial.

    With `holds` (a function deciding a rendered `.gc` instance, e.g. by explicit search), each
    instance only assumes a minimal subset of the fairness conditions: conditions are dropped
    greedily while the property still holds. Assuming less is a stronger property, so a proof of
    the instance proves the original one; the automaton gets much smaller."""
    ev = tr.ev
    d, scope = _resolve_def(ev, name)
    out = []
    try:
        if not _is_temporal(ev, d.body, scope.env):
            p = ev.to_formula(ev.ev(d.body, scope.env, Ctx()))  # a state predicate: initial states only
            aps = _APs(tr.ranges)
            good = aps.predicate(p)
            if good == "true":
                raise Tautology(name)
            prop = PropertyGC(name, "temporal", f"property {name} (initial states)", aps.preds, good)
            lower(prop, tr.ranges, bool(tr.boot_slot))
            return [prop]
        parts = _conjuncts(ev, d.body, scope.env)
        counts = {}
        for label, _, _ in parts:
            counts[label] = counts.get(label, 0) + 1
        seen = {}
        for k, (label, node, env) in enumerate(parts):
            if label and counts[label] > 1:  # several conjuncts under one binding
                seen[label] = seen.get(label, 0) + 1
                label = f"{label},{seen[label]}"
            pname = name if len(parts) == 1 else f"{name}[{label or k}]"
            fairness = list(tr.fairness)
            if holds is not None and fairness:
                for F in list(fairness):
                    trial = [G for G in fairness if G is not F]
                    prop = _instance(tr, pname, node, env, trial, tr.fairness)
                    if prop is None or holds(tr.render(prop)):
                        fairness = trial
            prop = _instance(tr, pname, node, env, fairness, fairness)
            if prop is not None:
                out.append(prop)
    except (TooBig, RecursionError) as e:
        raise TranslationError(f"too large: {e}")
    except (EvalError, RuntimeError) as e:
        raise TranslationError(f"{type(e).__name__}: {e}")
    if not out:
        raise Tautology(name)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec_dir", type=Path)
    ap.add_argument("module")
    ap.add_argument("cfg", type=Path)
    ap.add_argument("dump", type=Path, help="TLC `-dump dot` state graph of the model")
    ap.add_argument("--invariant", default=None)
    args = ap.parse_args(argv)
    cfg = parse_cfg(args.cfg.read_text())
    from tlc import read_graph
    states, _ = read_graph(args.dump)
    tr = build(args.spec_dir, args.module, cfg, states, f"{args.spec_dir.name}/{args.cfg.stem}")
    prop = invariant_property(tr, args.invariant) if args.invariant else None
    sys.stdout.write(tr.render(prop))
    return 0


if __name__ == "__main__":
    sys.exit(main())
