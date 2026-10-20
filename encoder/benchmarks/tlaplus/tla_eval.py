"""Symbolic evaluation of TLA+ over an integer-slot encoding of the state.

`Evaluator.ev` evaluates an expression to a concrete or symbolic value (`tla_sym`), with the
current state given symbolically (one value per variable, built from its layout). Constants
come from the TLC model; quantifiers, CHOOSE and set constructions range over concrete sets.
Whenever an operation needs a concrete operand that is symbolic (a function applied to a
symbolic argument, nonlinear arithmetic, ...), the operand is split into its possible concrete
values (using the values each slot takes), giving a `Cases` value.

`Evaluator.ev_action` grounds an action into branches (guard formula, assigned primed
variables), the way TLC enumerates successors: conjunctions are processed left to right,
`x' = e` and `x' \\in S` assign an unassigned `x'`, and disjunctions, existentials, IF and CASE
split into branches.
"""

from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass, field

from tla_front import Def, InstanceDecl, Loader, Module, named, parse_instance, text
from tla_sym import (BOTTOM, FALSE, TRUE, Cases, Formula, Lin, SBool, SEnum, SFn, SInt, SSet, TooBig, atom,
                     conj, disj, eq as lin_eq, iff, is_concrete, le as lin_le, lt as lin_lt, mk_bool, mk_cases,
                     mk_fn, mk_int, mk_set, neg)
from tla_values import EvalError, Fn, LazySet, is_int, show, sort_key, sorted_values

sys.setrecursionlimit(20000)

SPLIT_LIMIT = 4096      # max alternatives when splitting one symbolic value
BRANCH_LIMIT = 50000    # max branches of one action


class Unsupported(EvalError):
    """A construct outside the supported fragment."""


# --------------------------------------------------------------------------------------------
# Environments
# --------------------------------------------------------------------------------------------

class Thunk:
    """A lazily evaluated operator argument (call by name, memoized)."""

    __slots__ = ("node", "env", "ctx", "val", "done")

    def __init__(self, node, env, ctx):
        self.node, self.env, self.ctx = node, env, ctx
        self.done = False
        self.val = None


@dataclass
class Closure:
    """An operator value: LAMBDA, LET-defined operator, operator parameter, or definition."""
    params: list
    body: object         # node
    env: "Env"
    name: str = "<lambda>"


@dataclass
class Builtin:
    name: str


@dataclass
class InstanceRef:
    """A named instance `L == INSTANCE M WITH ...`, possibly parameterized."""
    d: Def
    scope: "Scope"
    env: "Env"


class Env:
    __slots__ = ("names", "parent", "scope")

    def __init__(self, names: dict, parent: "Env | None", scope: "Scope"):
        self.names, self.parent, self.scope = names, parent, scope

    def bind(self, names: dict) -> "Env":
        return Env(names, self, self.scope)

    def find_local(self, name):
        e = self
        while e is not None:
            if name in e.names:
                return True, e.names[name]
            e = e.parent
        return False, None


class Scope:
    """A module, as instantiated: its definitions, and how its constants/variables resolve."""

    def __init__(self, ev: "Evaluator", module: Module, subst: dict, outer: "Env | None"):
        self.ev = ev
        self.module = module
        self.subst = subst        # name -> (node, Env) for INSTANCE substitutions
        self.outer = outer        # Env of the instantiating module (implicit substitution)
        self._inst_scopes = {}
        self.env = Env({}, None, self)

    def instance_scope(self, inst: InstanceDecl) -> "Scope":
        key = id(inst)
        if key not in self._inst_scopes:
            m = self.ev.loader.load(inst.module)
            subst = {k: (n, self.env) for k, n in inst.subst.items()}
            self._inst_scopes[key] = Scope(self.ev, m, subst, self.env)
        return self._inst_scopes[key]

    def owner_scope(self, owner) -> "Scope":
        if owner is None:
            return self
        if isinstance(owner, InstanceDecl):
            return self.instance_scope(owner)
        _, inst, inner = owner
        return self.instance_scope(inst).owner_scope(inner)

    def lookup(self, name: str):
        hit = self.module.visible.get(name)
        if hit is not None:
            d, owner = hit
            return ("def", d, self.owner_scope(owner))
        if name in self.module.constants or name in self.module.variables:
            if name in self.subst:
                node, env = self.subst[name]
                return ("subst", node, env)
            if self.outer is not None:
                return ("outer", self.outer)
            if name in self.module.variables:
                return ("var", name)
            return ("const", name)
        if self.outer is not None:
            return ("outer", self.outer)
        return None


# --------------------------------------------------------------------------------------------
# Evaluation context and branches
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Ctx:
    assigned: dict | None = None   # primed (or, for Init, unprimed) variables assigned so far
    primed: bool = False           # evaluating inside (e)'
    init: bool = False             # evaluating Init: unprimed variables are being assigned


@dataclass
class Branch:
    guard: Formula
    assigned: dict = field(default_factory=dict)
    label: str = ""


# --------------------------------------------------------------------------------------------
# The evaluator
# --------------------------------------------------------------------------------------------

class Evaluator:
    def __init__(self, loader: Loader, root: Module, constants: dict, overrides: dict,
                 cur: dict | None = None, slot_values: dict | None = None):
        self.loader = loader
        self.root = root
        self.constants = constants          # cfg constants (concrete)
        self.overrides = overrides          # cfg `<-` substitutions: name -> definition name
        self.cur = cur or {}                # var -> symbolic current value
        self.slot_values = slot_values or {}  # slot -> sorted possible values (for splitting)
        self.scope = Scope(self, root, {}, None)
        self.state_reads = 0
        self._cache = {}
        self.recursive = set()  # RECURSIVE operators of LET blocks

    # ---------------------------------------------------------------- name resolution

    def resolve(self, name: str, env: Env):
        found, v = env.find_local(name)
        if found:
            return ("local", v)
        scope = env.scope
        while True:
            if scope is self.scope:  # the model's constants and `<-` overrides
                if name in self.constants:
                    return ("value", self.constants[name])
                if name in self.overrides:
                    return self.resolve(self.overrides[name], self.scope.env)
            hit = scope.lookup(name)
            if hit is None:
                break
            if hit[0] == "outer":
                found, v = hit[1].find_local(name)
                if found:
                    return ("local", v)
                scope = hit[1].scope
                continue
            return hit
        if name in BUILTINS:
            return ("builtin", Builtin(name))
        raise EvalError(f"unknown identifier {name}")

    # ---------------------------------------------------------------- helpers

    def force(self, v):
        if isinstance(v, Thunk):
            if not v.done:
                v.val = self.ev(v.node, v.env, v.ctx)
                v.done = True
            return v.val
        return v

    def call_def(self, d: Def, scope: Scope, args: list, ctx: Ctx, node_for_err=None):
        """Evaluate a definition applied to argument thunks/values."""
        if d.kind == "fn":
            return self.fn_def_value(d, scope, ctx)
        if d.kind == "inst":
            raise Unsupported(f"instance {d.name} used as a value")
        if len(args) != len(d.params):
            raise EvalError(f"{d.name} expects {len(d.params)} arguments")
        if not d.params:
            key = (id(d), id(scope), ctx.primed, ctx.init)
            hit = self._cache.get(key)
            if hit is not None:
                return hit[0]
            reads = self.state_reads
            v = self.ev(d.body, scope.env, ctx)
            if self.state_reads == reads:
                self._cache[key] = (v,)
            return v
        if d.name in scope.module.recursive or d.name in self.recursive:
            # A recursive operator on a symbolic argument would unfold forever (both branches of
            # its base-case test are evaluated): evaluate it on each concrete argument instead.
            vals = [self.force(a) if isinstance(a, Thunk) else a for a in args]
            sym = tuple(i for i, v in enumerate(vals) if not isinstance(v, (Thunk, Closure, Builtin, tuple))
                        and not is_concrete(v))
            if sym:
                return self.lift_sym(lambda *vs: self._call_def_body(d, scope, list(vs), ctx), *vals, keys=sym)
            args = vals
        return self._call_def_body(d, scope, args, ctx)

    def _call_def_body(self, d, scope, args, ctx):
        names = {}
        for p, a in zip(d.params, args):
            names[p if isinstance(p, str) else p[0]] = a
        return self.ev(d.body, Env(names, scope.env, scope), ctx)

    def fn_def_value(self, d: Def, scope: Scope, ctx: Ctx):
        """`f[x \\in S] == e`: a (possibly recursive) function; evaluated eagerly when its
        domain is finite, else as a lazily memoized function."""
        key = ("fn", id(d), id(scope), ctx.primed, ctx.init)
        hit = self._cache.get(key)
        if hit is not None:
            return hit[0]
        reads = self.state_reads
        names_list, sets = self._bounds(d.bounds, scope.env, ctx)
        lazy = LazyFn(self, d, scope, ctx, names_list, sets)
        if all(isinstance(s, frozenset) for s in sets) and _product_size(sets) <= 100000:
            self._cache[key] = (lazy,)  # recursion inside the body sees the lazy function
            v = lazy.materialize()
        else:
            v = lazy
        if self.state_reads == reads:
            self._cache[key] = (v,)
        else:
            self._cache.pop(key, None)
        return v

    def read_var(self, name: str, ctx: Ctx):
        self.state_reads += 1
        if ctx.primed or ctx.init:
            if ctx.assigned is not None and name in ctx.assigned:
                return ctx.assigned[name]
            raise Unsupported(f"variable {name}{chr(39) if ctx.primed else ''} read before it is assigned")
        if name not in self.cur:
            raise EvalError(f"no value for variable {name}")
        return self.cur[name]

    def constant(self, name: str):
        if name in self.constants:
            return self.constants[name]
        raise EvalError(f"constant {name} has no value in the model")

    # ---------------------------------------------------------------- splitting

    def concretize(self, v, limit: int = SPLIT_LIMIT) -> list:
        """[(condition, concrete value)] covering v."""
        if is_concrete(v):
            return [(TRUE, v)]
        if v is BOTTOM:
            return []
        if isinstance(v, SBool):
            return [(v.f, True), (neg(v.f), False)]
        if isinstance(v, SEnum):
            codes = self.slot_values.get(v.slot, range(len(v.table)))
            return [(atom(Lin.var(v.slot) - i, "=="), v.table[i]) for i in codes]
        if isinstance(v, SInt):
            slots = sorted(v.lin.terms)
            doms = [self.slot_values[s] for s in slots]
            if _product_size(doms) > limit:
                raise TooBig(f"splitting {v.lin.render()} over {_product_size(doms)} values")
            out = {}
            for combo in itertools.product(*doms):
                val = v.lin.const + sum(v.lin.terms[s] * c for s, c in zip(slots, combo))
                cond = conj(*(atom(Lin.var(s) - c, "==") for s, c in zip(slots, combo)))
                out.setdefault(val, []).append(cond)
            return [(disj(*cs), val) for val, cs in out.items()]
        if isinstance(v, Cases):
            out = []
            for c, x in v.alts:
                for c2, y in self.concretize(x, limit):
                    cc = conj(c, c2)
                    if cc is not FALSE:
                        out.append((cc, y))
                        if len(out) > limit:
                            raise TooBig("too many cases")
            return out
        if isinstance(v, SFn):
            keys = list(v.map)
            parts = [self.concretize(v.map[k], limit) for k in keys]
            if _product_size(parts) > limit:
                raise TooBig(f"splitting a function into {_product_size(parts)} values")
            out = []
            for combo in itertools.product(*parts):
                c = conj(*(p[0] for p in combo))
                if c is not FALSE:
                    out.append((c, Fn({k: p[1] for k, p in zip(keys, combo)})))
            return out
        if isinstance(v, SSet):
            fixed = frozenset(k for k, f in v.mem.items() if f is TRUE)
            free = [(k, f) for k, f in v.mem.items() if f is not TRUE]
            if 2 ** len(free) > limit:
                raise TooBig(f"splitting a set over {len(free)} symbolic elements")
            out = []
            for bits in itertools.product((True, False), repeat=len(free)):
                c = conj(*(f if b else neg(f) for (k, f), b in zip(free, bits)))
                if c is not FALSE:
                    out.append((c, fixed | frozenset(k for (k, f), b in zip(free, bits) if b)))
            return out
        raise EvalError(f"cannot split {v!r}")

    def unenum(self, v):
        """An enumeration-coded value as explicit cases (so that set and function operations,
        which handle Cases, apply to it)."""
        return mk_cases(self.concretize(v)) if isinstance(v, SEnum) else v

    def lift(self, fn, *args):
        """Apply a concrete function to possibly symbolic arguments (by case splitting)."""
        if all(is_concrete(a) for a in args):
            return fn(*args)
        if any(a is BOTTOM for a in args):
            return BOTTOM
        parts = [self.concretize(a) for a in args]
        if _product_size(parts) > SPLIT_LIMIT:
            raise TooBig("too many case combinations")
        alts = []
        for combo in itertools.product(*parts):
            c = conj(*(p[0] for p in combo))
            if c is FALSE:
                continue
            try:
                alts.append((c, fn(*(p[1] for p in combo))))
            except Unsupported:
                raise
            except EvalError:
                alts.append((c, BOTTOM))
        return mk_cases(alts)

    def map_cases(self, v, fn):
        """Apply fn to each alternative of a Cases value (fn may return symbolic values)."""
        if isinstance(v, Cases):
            return mk_cases([(c, self.map_cases(x, fn)) for c, x in v.alts])
        if v is BOTTOM:
            return BOTTOM
        try:
            return fn(v)
        except Unsupported:
            raise
        except EvalError:
            return BOTTOM

    # ---------------------------------------------------------------- symbolic primitives

    def to_formula(self, v) -> Formula:
        if v is True:
            return TRUE
        if v is False:
            return FALSE
        if isinstance(v, SBool):
            return v.f
        if v is BOTTOM:
            return FALSE
        if isinstance(v, Cases):
            return disj(*(conj(c, self.to_formula(x)) for c, x in v.alts))
        raise EvalError(f"expected a boolean, got {show(v) if is_concrete(v) else v!r}")

    def to_lin(self, v) -> Lin | None:
        if is_int(v):
            return Lin({}, v)
        if isinstance(v, SInt):
            return v.lin
        return None

    def equal(self, a, b) -> Formula:
        a, b = self.force(a), self.force(b)
        if a is BOTTOM or b is BOTTOM:
            return FALSE
        if is_concrete(a) and is_concrete(b):
            return TRUE if values_equal(a, b) else FALSE
        if isinstance(a, Cases):
            return disj(*(conj(c, self.equal(x, b)) for c, x in a.alts))
        if isinstance(b, Cases):
            return disj(*(conj(c, self.equal(a, x)) for c, x in b.alts))
        la, lb = self.to_lin(a), self.to_lin(b)
        if la is not None and lb is not None:
            return lin_eq(la, lb)
        if isinstance(a, SBool) or isinstance(b, SBool):
            if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, SBool) and isinstance(b, SBool):
                return iff(self.to_formula(a), self.to_formula(b))
            return FALSE
        if isinstance(b, SEnum) and not isinstance(a, SEnum):
            a, b = b, a
        if isinstance(a, SEnum):
            codes = self.slot_values.get(a.slot, range(len(a.table)))
            if is_concrete(b):
                return disj(*(atom(Lin.var(a.slot) - i, "==") for i in codes if values_equal(a.table[i], b)))
            if isinstance(b, SEnum) and b.table == a.table:
                return lin_eq(Lin.var(a.slot), Lin.var(b.slot))
            return disj(*(conj(atom(Lin.var(a.slot) - i, "=="), self.equal(a.table[i], b)) for i in codes))
        if isinstance(a, (SFn, Fn)) and isinstance(b, (SFn, Fn)):
            ma = a.map if isinstance(a, SFn) else a._map
            mb = b.map if isinstance(b, SFn) else b._map
            if set(ma) != set(mb):
                return FALSE
            return conj(*(self.equal(ma[k], mb[k]) for k in ma))
        if isinstance(a, (SSet, frozenset)) and isinstance(b, (SSet, frozenset)):
            ma = self.set_mem(a)
            mb = self.set_mem(b)
            return conj(*(iff(ma.get(k, FALSE), mb.get(k, FALSE)) for k in set(ma) | set(mb)))
        if isinstance(a, SInt) or isinstance(b, SInt) or isinstance(a, SSet) or isinstance(b, SSet) \
                or isinstance(a, SFn) or isinstance(b, SFn):
            return FALSE  # different kinds of values
        return self.to_formula(self.lift(lambda x, y: values_equal(x, y), a, b))

    def set_mem(self, s) -> dict:
        if isinstance(s, frozenset):
            return {k: TRUE for k in s}
        if isinstance(s, SSet):
            return s.mem
        raise EvalError("expected a finite set")

    def member(self, e, s) -> Formula:
        e, s = self.force(e), self.unenum(self.force(s))
        if e is BOTTOM or s is BOTTOM:
            return FALSE
        if isinstance(s, Cases):
            return disj(*(conj(c, self.member(e, x)) for c, x in s.alts))
        if isinstance(s, LazySet):
            if is_concrete(e):
                return TRUE if s.member(e) else FALSE
            lin = self.to_lin(e)
            if lin is not None and s.name in ("Nat", "Int"):
                return TRUE if s.name == "Int" else lin_le(Lin({}, 0), lin)
            return self.to_formula(self.lift(lambda x: s.member(x), e))
        if isinstance(s, frozenset):
            if is_concrete(e):
                return TRUE if any(values_equal(e, x) for x in s) else FALSE
            lin = self.to_lin(e)
            if lin is not None and s and all(is_int(x) for x in s):
                lo, hi = min(s), max(s)
                if len(s) == hi - lo + 1:
                    return conj(lin_le(Lin({}, lo), lin), lin_le(lin, Lin({}, hi)))
            return disj(*(self.equal(e, x) for x in s))
        if isinstance(s, SSet):
            if is_concrete(e):
                return next((f for k, f in s.mem.items() if values_equal(k, e)), FALSE)
            return disj(*(conj(f, self.equal(e, k)) for k, f in s.mem.items()))
        raise EvalError(f"\\in applied to a non-set {s!r}")

    def apply(self, f, k):
        f, k = self.force(f), self.force(k)
        if f is BOTTOM or k is BOTTOM:
            return BOTTOM
        if isinstance(f, Cases):
            return mk_cases([(c, self._try(self.apply, x, k)) for c, x in f.alts])
        if isinstance(f, (LazyFn,)):
            if is_concrete(k):
                return f.apply(k)
            return self.lift(f.apply, k)
        if isinstance(f, Closure):
            return self.call_closure(f, [k], Ctx())
        if isinstance(f, SEnum):
            return self.apply(self.unenum(f), k)
        if isinstance(f, (SInt, SBool, SSet)):
            raise EvalError("applying a non-function")
        if not isinstance(f, (Fn, SFn)):
            raise EvalError(f"applying a non-function {f!r}")
        m = f._map if isinstance(f, Fn) else f.map
        if is_concrete(k):
            for key in (k,):
                if key in m and _same_kind(key, k):
                    return m[key]
            raise EvalError(f"function applied outside its domain: {show(k)}")
        alts = []
        for key, val in m.items():
            c = self.equal(k, key)
            if c is not FALSE:
                alts.append((c, val))
        return mk_cases(alts) if alts else BOTTOM

    def _try(self, fn, *args):
        try:
            return fn(*args)
        except Unsupported:
            raise
        except EvalError:
            return BOTTOM

    def call_closure(self, c: Closure, args: list, ctx: Ctx):
        if len(args) != len(c.params):
            raise EvalError(f"{c.name} expects {len(c.params)} arguments")
        names = {p if isinstance(p, str) else p[0]: a for p, a in zip(c.params, args)}
        return self.ev(c.body, c.env.bind(names), ctx)

    def call_operator(self, op, args: list, ctx: Ctx):
        """Apply an operator value (from a higher-order parameter) to evaluated args."""
        op = self.force(op)
        if isinstance(op, Closure):
            return self.call_closure(op, args, ctx)
        if isinstance(op, Builtin):
            return call_builtin(self, op.name, [self.force(a) for a in args], ctx, None, None)
        if isinstance(op, tuple) and op[0] == "def":
            return self.call_def(op[1], op[2], args, ctx)
        raise EvalError(f"not an operator: {op!r}")

    # ---------------------------------------------------------------- expressions

    def ev(self, node, env: Env, ctx: Ctx = Ctx()):
        t = node.type
        m = getattr(self, "ev_" + t, None)
        if m is None:
            raise Unsupported(f"unsupported construct {t}: {text(node)[:60]!r}")
        return m(node, env, ctx)

    def ev_parentheses(self, node, env, ctx):
        return self.ev(named(node)[0], env, ctx)

    def ev_nat_number(self, node, env, ctx):
        return int(text(node))

    def ev_string(self, node, env, ctx):
        s = text(node)[1:-1]
        return s.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n").replace("\\t", "\t")

    def ev_boolean(self, node, env, ctx):
        return text(node) == "TRUE"

    def ev_boolean_set(self, node, env, ctx):
        return frozenset({False, True})

    def ev_nat_number_set(self, node, env, ctx):
        if "Nat" in self.overrides:
            return self.ev_name(self.overrides["Nat"], self.scope.env, ctx, [], node)
        return NAT

    def ev_int_number_set(self, node, env, ctx):
        if "Int" in self.overrides:
            return self.ev_name(self.overrides["Int"], self.scope.env, ctx, [], node)
        return INT

    def ev_label(self, node, env, ctx):
        return self.ev(named(node)[-1], env, ctx)

    def ev_string_set(self, node, env, ctx):
        return STRING

    def ev_identifier_ref(self, node, env, ctx):
        return self.ev_name(text(node), env, ctx, [], node)

    def ev_name(self, name, env, ctx, args, node):
        kind, *rest = self.resolve(name, env)
        if kind == "local":
            v = rest[0]
            if isinstance(v, Thunk):
                if args:
                    return self.call_operator(self.force(v), args, ctx)
                return self.force(v)
            if isinstance(v, Closure):
                if args or not v.params:
                    return self.call_closure(v, args, ctx)
                return v
            if isinstance(v, (Builtin, tuple)) and args:
                return self.call_operator(v, args, ctx)
            if args:
                return self.apply_args(v, args, ctx)
            return v
        if kind == "def":
            d, scope = rest
            if d.params and not args:
                return ("def", d, scope)  # operator passed as an argument
            return self.call_def(d, scope, args, ctx, node)
        if kind == "var":
            v = self.read_var(rest[0], ctx)
            return self.apply_args(v, args, ctx) if args else v
        if kind == "const":
            v = self.constant(rest[0])
            return self.apply_args(v, args, ctx) if args else v
        if kind == "value":
            return self.apply_args(rest[0], args, ctx) if args else rest[0]
        if kind == "subst":
            snode, senv = rest
            v = self.ev(snode, senv, ctx)
            return self.call_operator(v, args, ctx) if args else v
        if kind == "builtin":
            if not args and rest[0].name in BUILTIN_CONSTANTS:
                return BUILTIN_CONSTANTS[rest[0].name]
            if not args:
                return rest[0]
            return call_builtin(self, name, [self.force(a) for a in args], ctx, node, env)
        raise EvalError(f"cannot evaluate {name}")

    def apply_args(self, v, args, ctx):
        if isinstance(v, (Closure, Builtin, tuple)):
            return self.call_operator(v, args, ctx)
        raise EvalError("applying a non-operator")

    def _arg(self, n, env, ctx):
        if n.type == "lambda":
            ids = [text(c) for c in n.named_children if c.type == "identifier"]
            return Closure(ids, named(n)[-1], env)
        return Thunk(n, env, ctx)

    def ev_bound_op(self, node, env, ctx):
        name_node = node.child_by_field_name("name")
        args = [self._arg(c, env, ctx) for c in self._op_args(node)]
        return self.ev_name(text(name_node), env, ctx, args, node)

    def _op_args(self, node):
        out = []
        for i, c in enumerate(node.children):
            if node.field_name_for_child(i) == "parameter" and c.is_named:
                out.append(c)
        return out

    def ev_prefixed_op(self, node, env, ctx):
        scope, env2 = self.instance_target(node, env, ctx)
        inner = named(node)[-1]
        if inner.type == "bound_op":
            name = text(inner.child_by_field_name("name"))
            args = [self._arg(c, env, ctx) for c in self._op_args(inner)]
        elif inner.type == "identifier_ref":
            name, args = text(inner), []
        else:
            raise Unsupported(f"instance reference {text(node)[:40]}")
        return self.ev_name(name, env2, ctx, args, node)

    def instance_target(self, node, env, ctx):
        """`L!` or `L(a)!` -> the instance's Scope and an Env to look names up in."""
        prefix = named(node)[0]
        comps = [c for c in named(prefix) if c.type == "subexpr_component"]
        if len(comps) != 1:
            raise Unsupported(f"nested instance reference {text(node)[:40]}")
        comp = named(comps[0])[0]
        if comp.type == "identifier_ref":
            iname, args = text(comp), []
        else:
            iname = text(comp.child_by_field_name("name"))
            args = [Thunk(c, env, ctx) for c in self._op_args(comp)]
        kind, *rest = self.resolve(iname, env)
        if kind != "def" or rest[0].kind != "inst":
            raise Unsupported(f"{iname} is not a named instance")
        d, scope = rest
        inst = parse_instance(d.body)
        penv = Env({(p if isinstance(p, str) else p[0]): a for p, a in zip(d.params, args)},
                   scope.env, scope)
        key = (id(d), id(scope), tuple(id(a) for a in args))
        sc = scope._inst_scopes.get(key)
        if sc is None or args:
            m = self.loader.load(inst.module)
            sc = Scope(self, m, {k: (n, penv) for k, n in inst.subst.items()}, penv)
            if not args:
                scope._inst_scopes[key] = sc
        return sc, sc.env

    def ev_subexpression(self, node, env, ctx):
        """`Op!n` / `Op(args)!n`: the n-th conjunct (or disjunct, or operand) of Op's body."""
        prefix = named(node)[0]
        comps = [c for c in named(prefix) if c.type == "subexpr_component"]
        navs = [c for c in named(node) if c.type == "subexpr_tree_nav"]
        if len(comps) != 1 or len(navs) != 1:
            raise Unsupported(f"subexpression {text(node)[:40]}")
        comp = named(comps[0])[0]
        if comp.type == "identifier_ref":
            name, args = text(comp), []
        elif comp.type == "bound_op":
            name = text(comp.child_by_field_name("name"))
            args = [self._arg(c, env, ctx) for c in self._op_args(comp)]
        else:
            raise Unsupported(f"subexpression {text(node)[:40]}")
        kind, *rest = self.resolve(name, env)
        if kind != "def" or rest[0].kind != "op":
            raise Unsupported(f"subexpression of {name}")
        d, scope = rest
        body = d.body
        for nav in named(navs[0]):
            if nav.type != "child_id":
                raise Unsupported(f"subexpression {text(node)[:40]}")
            k = int(text(nav))
            while body.type == "parentheses":
                body = named(body)[0]
            if body.type in ("conj_list", "disj_list"):
                body = named(named(body)[k - 1])[-1]
            elif body.type == "bound_infix_op":
                body = body.child_by_field_name("lhs" if k == 1 else "rhs")
            else:
                raise Unsupported(f"subexpression {text(node)[:40]}")
        names_d = {(p if isinstance(p, str) else p[0]): a for p, a in zip(d.params, args)}
        return self.ev(body, Env(names_d, scope.env, scope), ctx)

    def ev_lambda(self, node, env, ctx):
        ids = [text(c) for c in node.named_children if c.type == "identifier"]
        return Closure(ids, named(node)[-1], env)

    def ev_prev_func_val(self, node, env, ctx):
        found, v = env.find_local("@")
        if not found:
            raise EvalError("@ outside EXCEPT")
        return v

    def ev_bound_postfix_op(self, node, env, ctx):
        sym = node.child_by_field_name("symbol").type
        if sym == "prime":
            if ctx.primed:
                raise Unsupported("double prime")
            return self.ev(node.child_by_field_name("lhs"), env, Ctx(ctx.assigned, True, False))
        raise Unsupported(f"postfix operator {sym}")

    def ev_bound_prefix_op(self, node, env, ctx):
        sym = node.child_by_field_name("symbol").type
        rhs = node.child_by_field_name("rhs")
        if sym == "lnot":
            return mk_bool(neg(self.to_formula(self.ev(rhs, env, ctx))))
        if sym == "negative":
            v = self.ev(rhs, env, ctx)
            lin = self.to_lin(v)
            if lin is not None:
                return mk_int(-lin)
            return self.lift(lambda x: -_int(x), v)
        if sym == "domain":
            v = self.unenum(self.ev(rhs, env, ctx))

            def dom(f):
                if isinstance(f, Fn):
                    return f.domain()
                if isinstance(f, SFn):
                    return frozenset(f.map)
                if isinstance(f, LazyFn):
                    return f.domain_set()
                raise EvalError("DOMAIN of a non-function")
            return self.map_cases(v, dom)
        if sym == "powerset":
            v = self.ev(rhs, env, ctx)
            return self.lift(_powerset, v)
        if sym == "union":
            v = self.ev(rhs, env, ctx)
            return self.lift(lambda s: frozenset(x for y in _finite(s) for x in _finite(y)), v)
        if sym == "unchanged":
            return mk_bool(self.unchanged_formula(rhs, env, ctx))
        if sym == "enabled":
            return mk_bool(self.enabled(rhs, env, ctx))
        raise Unsupported(f"prefix operator {sym}")

    def ev_bound_infix_op(self, node, env, ctx):
        sym = node.child_by_field_name("symbol")
        st = sym.type
        lhs, rhs = node.child_by_field_name("lhs"), node.child_by_field_name("rhs")
        if st == "land":
            a = self.to_formula(self.ev(lhs, env, ctx))
            if a is FALSE:
                return False
            return mk_bool(conj(a, self.to_formula(self.ev(rhs, env, ctx))))
        if st == "lor":
            a = self.to_formula(self.ev(lhs, env, ctx))
            if a is TRUE:
                return True
            return mk_bool(disj(a, self.to_formula(self.ev(rhs, env, ctx))))
        if st == "implies":
            a = self.to_formula(self.ev(lhs, env, ctx))
            if a is FALSE:
                return True
            return mk_bool(disj(neg(a), self.to_formula(self.ev(rhs, env, ctx))))
        if st in ("iff", "equiv"):
            return mk_bool(iff(self.to_formula(self.ev(lhs, env, ctx)),
                               self.to_formula(self.ev(rhs, env, ctx))))
        if st == "times":
            factors = self._times_factors(node)
            vals = [self.ev(f, env, ctx) for f in factors]
            return self.lift(lambda *ss: frozenset(Fn.tuple(t) for t in itertools.product(
                *(sorted_values(_finite(s)) for s in ss))), *vals)
        a = self.ev(lhs, env, ctx)
        b = self.ev(rhs, env, ctx)
        return self.infix(st, text(sym), a, b, env, ctx, node)

    def _times_factors(self, node):
        out = []
        for side in ("lhs", "rhs"):
            c = node.child_by_field_name(side)
            if c.type == "bound_infix_op" and c.child_by_field_name("symbol").type == "times":
                out.extend(self._times_factors(c))
            else:
                out.append(c)
        return out

    def infix(self, st, sym_text, a, b, env, ctx, node):
        if st == "eq":
            return mk_bool(self.equal(a, b))
        if st == "neq":
            return mk_bool(neg(self.equal(a, b)))
        if st == "in":
            return mk_bool(self.member(a, b))
        if st == "notin":
            return mk_bool(neg(self.member(a, b)))
        if st in ("plus", "minus"):
            la, lb = self.to_lin(a), self.to_lin(b)
            if la is not None and lb is not None:
                return mk_int(la + lb if st == "plus" else la - lb)
            return self.lift(lambda x, y: _int(x) + _int(y) if st == "plus" else _int(x) - _int(y), a, b)
        if st == "mul":
            la, lb = self.to_lin(a), self.to_lin(b)
            if la is not None and lb is not None and (la.is_const() or lb.is_const()):
                return mk_int(lb.scale(la.const) if la.is_const() else la.scale(lb.const))
            return self.lift(lambda x, y: _int(x) * _int(y), a, b)
        if st in ("lt", "leq", "gt", "geq"):
            la, lb = self.to_lin(a), self.to_lin(b)
            if la is None or lb is None:
                return self.lift(lambda x, y: _cmp(st, x, y), a, b)
            f = {"lt": lin_lt(la, lb), "leq": lin_le(la, lb), "gt": lin_lt(lb, la), "geq": lin_le(lb, la)}[st]
            return mk_bool(f)
        if st == "div":
            return self.lift(lambda x, y: _floordiv(_int(x), _int(y)), a, b)
        if st == "mod":
            return self.lift(lambda x, y: _mod(_int(x), _int(y)), a, b)
        if st == "pow":
            return self.lift(lambda x, y: _int(x) ** _int(y), a, b)
        if st == "dots_2":
            return self.lift(lambda x, y: frozenset(range(_int(x), _int(y) + 1)), a, b)
        if st == "cup":
            return self.set_op(a, b, "cup")
        if st == "cap":
            return self.set_op(a, b, "cap")
        if st == "setminus":
            return self.set_op(a, b, "minus")
        if st == "subseteq":
            return mk_bool(self.subseteq(a, b))
        if st == "circ":
            if is_concrete(a) and is_concrete(b):
                return Fn.tuple(_seq(a) + _seq(b))
            return _seq_builtin(self, "\\o", [a, b])
        if st == "map_to":
            return self.lift_sym(lambda x, y: SFn({x: y}) if not is_concrete(y) else Fn({x: y}), a, b, keys=(0,))
        if st == "compose":
            return self.lift(_compose, a, b)
        if st == "ampamp" or sym_text == "\\o":
            return self.lift(lambda x, y: Fn.tuple(_seq(x) + _seq(y)), a, b)
        # User-defined infix operator.
        kind, *rest = self.resolve(sym_text, env)
        if kind == "def":
            return self.call_def(rest[0], rest[1], [a, b], ctx)
        if kind == "local":
            return self.call_operator(rest[0], [a, b], ctx)
        raise Unsupported(f"infix operator {sym_text}")

    def lift_sym(self, fn, *args, keys=()):
        """Like lift, but only the arguments at positions `keys` need to be concrete."""
        args = list(args)
        if all(is_concrete(args[i]) for i in keys):
            return fn(*args)
        parts = [self.concretize(args[i]) if i in keys else [(TRUE, args[i])] for i in range(len(args))]
        alts = []
        for combo in itertools.product(*parts):
            c = conj(*(p[0] for p in combo))
            if c is not FALSE:
                alts.append((c, self._try(fn, *(p[1] for p in combo))))
        return mk_cases(alts)

    def subseteq(self, a, b) -> Formula:
        a, b = self.unenum(self.force(a)), self.unenum(self.force(b))
        if isinstance(a, Cases):
            return disj(*(conj(c, self.subseteq(x, b)) for c, x in a.alts))
        if isinstance(a, (frozenset, SSet)):
            return conj(*(disj(neg(f), self.member(k, b)) for k, f in self.set_mem(a).items()))
        raise EvalError("\\subseteq of a non-set")

    def set_op(self, a, b, op):
        a, b = self.unenum(self.force(a)), self.unenum(self.force(b))
        if is_concrete(a) and is_concrete(b):
            if isinstance(a, LazySet) or isinstance(b, LazySet):
                if op == "cap" and isinstance(b, LazySet) and isinstance(a, frozenset):
                    return frozenset(x for x in a if b.member(x))
                if op == "minus" and isinstance(b, LazySet) and isinstance(a, frozenset):
                    return frozenset(x for x in a if not b.member(x))
                if op == "cup":
                    return LazySet(f"({a!r} \\cup {b!r})", lambda x: _lazy_mem(a, x) or _lazy_mem(b, x))
                raise Unsupported("set operation on infinite sets")
            return {"cup": a | b, "cap": a & b, "minus": a - b}[op]
        if isinstance(a, Cases) or isinstance(b, Cases):
            return self.lift_sym(lambda x, y: self.set_op(x, y, op), a, b,
                                 keys=tuple(i for i, v in enumerate((a, b)) if isinstance(v, Cases)))
        if a is BOTTOM or b is BOTTOM:
            return BOTTOM
        ma = self.set_mem(a) if not isinstance(a, LazySet) else None
        if isinstance(b, LazySet):
            if op == "cap":
                return mk_set({k: conj(f, TRUE if b.member(k) else FALSE) for k, f in ma.items()})
            if op == "minus":
                return mk_set({k: conj(f, FALSE if b.member(k) else TRUE) for k, f in ma.items()})
            raise Unsupported("union with an infinite set")
        mb = self.set_mem(b)
        keys = list(dict.fromkeys(list(ma) + list(mb)))
        if op == "cup":
            return mk_set({k: disj(ma.get(k, FALSE), mb.get(k, FALSE)) for k in keys})
        if op == "cap":
            return mk_set({k: conj(ma.get(k, FALSE), mb.get(k, FALSE)) for k in keys})
        return mk_set({k: conj(ma.get(k, FALSE), neg(mb.get(k, FALSE))) for k in ma})

    # ---- bindings

    def _bounds(self, bounds, env, ctx):
        """quantifier_bound nodes -> ([names or tuple-of-names per bound var], [sets])."""
        names, sets = [], []
        for b in bounds:
            ids = []
            tup = None
            for c in b.named_children:
                if c.type == "identifier":
                    ids.append(text(c))
                elif c.type == "tuple_of_identifiers":
                    tup = tuple(text(x) for x in c.named_children if x.type == "identifier")
            s = self.force(self.ev(named(b)[-1], env, ctx))
            if tup is not None:
                names.append(tup)
                sets.append(s)
            for n in ids:
                names.append(n)
                sets.append(s)
        return names, sets

    def _bind(self, name, v, out: dict):
        if isinstance(name, tuple):
            vs = _seq(v)
            if len(vs) != len(name):
                raise EvalError("tuple binding arity mismatch")
            for n, x in zip(name, vs):
                out[n] = x
        else:
            out[name] = v

    def enumerate_bindings(self, bounds, env, ctx):
        """[(condition, env)] for all bindings of a quantifier's bounds."""
        names, sets = self._bounds(bounds, env, ctx)
        per = []
        for n, s in zip(names, sets):
            s = self.unenum(s)
            if s is BOTTOM:  # undefined (only in states TLC never evaluates this in)
                return []
            if isinstance(s, frozenset):
                per.append([(TRUE, x) for x in sorted_values(s)])
            elif isinstance(s, SSet):
                per.append([(f, k) for k, f in sorted(s.mem.items(), key=lambda kv: sort_key(kv[0]))])
            elif isinstance(s, Cases):
                alts = []
                for c, x in self.concretize(s):
                    alts.extend((c, y) for y in sorted_values(_finite(x)))
                per.append(alts)
            elif isinstance(s, LazySet) and s.elems is not None:
                per.append([(TRUE, x) for x in sorted_values(s.elems())])
            else:
                raise Unsupported(f"quantifier over {s!r}")
        if _product_size(per) > BRANCH_LIMIT:
            raise TooBig("too many quantifier bindings")
        out = []
        for combo in itertools.product(*per):
            c = conj(*(p[0] for p in combo))
            if c is FALSE:
                continue
            names_d = {}
            for n, (_, x) in zip(names, combo):
                self._bind(n, x, names_d)
            out.append((c, env.bind(names_d)))
        return out

    def ev_bounded_quantification(self, node, env, ctx):
        q = node.child_by_field_name("quantifier").type
        bounds = [c for c in node.named_children if c.type == "quantifier_bound"]
        body = named(node)[-1]
        parts = []
        for c, e2 in self.enumerate_bindings(bounds, env, ctx):
            f = self.to_formula(self.ev(body, e2, ctx))
            if q == "forall":
                parts.append(disj(neg(c), f))
                if parts[-1] is FALSE:
                    return False
            else:
                parts.append(conj(c, f))
                if parts[-1] is TRUE:
                    return True
        return mk_bool(conj(*parts) if q == "forall" else disj(*parts))

    def ev_choose(self, node, env, ctx):
        ids = [c for c in node.named_children if c.type in ("identifier", "tuple_of_identifiers")]
        kids = named(node)
        if not any(c.type == "set_in" for c in node.named_children):
            raise Unsupported("unbounded CHOOSE")
        name = text(ids[0]) if ids[0].type == "identifier" else \
            tuple(text(x) for x in ids[0].named_children if x.type == "identifier")
        s = self.force(self.ev(kids[-2], env, ctx))
        body = kids[-1]
        cands = self.enumerate_set(s)
        alts, none_before = [], TRUE
        for c, x in cands:
            names_d = {}
            self._bind(name, x, names_d)
            f = conj(c, self.to_formula(self.ev(body, env.bind(names_d), ctx)))
            if f is FALSE:
                continue
            alts.append((conj(none_before, f), x))
            none_before = conj(none_before, neg(f))
            if none_before is FALSE:
                break
        if none_before is not FALSE:
            alts.append((none_before, BOTTOM))
        return mk_cases(alts)

    def enumerate_set(self, s):
        s = self.unenum(s)
        if s is BOTTOM:
            return []
        if isinstance(s, frozenset):
            return [(TRUE, x) for x in sorted_values(s)]
        if isinstance(s, SSet):
            return [(f, k) for k, f in sorted(s.mem.items(), key=lambda kv: sort_key(kv[0]))]
        if isinstance(s, Cases):
            out = []
            for c, x in self.concretize(s):
                out.extend((c, y) for y in sorted_values(_finite(x)))
            return out
        if isinstance(s, LazySet) and s.elems is not None:
            return [(TRUE, x) for x in sorted_values(s.elems())]
        raise Unsupported(f"enumerating {s!r}")

    def ev_if_then_else(self, node, env, ctx):
        c = self.to_formula(self.ev(node.child_by_field_name("if"), env, ctx))
        if c is TRUE:
            return self.ev(node.child_by_field_name("then"), env, ctx)
        if c is FALSE:
            return self.ev(node.child_by_field_name("else"), env, ctx)
        return mk_cases([(c, self._guarded(node.child_by_field_name("then"), env, ctx)),
                         (neg(c), self._guarded(node.child_by_field_name("else"), env, ctx))])

    def _guarded(self, node, env, ctx):
        try:
            return self.ev(node, env, ctx)
        except Unsupported:
            raise
        except EvalError:
            return BOTTOM

    def ev_case(self, node, env, ctx):
        alts, none_before = [], TRUE
        for arm in named(node):
            if arm.type == "case_arm":
                parts = [c for c in named(arm) if c.type != "case_arrow"]
                c = self.to_formula(self.ev(parts[0], env, ctx))
                cond = conj(none_before, c)
                if cond is not FALSE:
                    alts.append((cond, self._guarded(parts[-1], env, ctx)))
                none_before = conj(none_before, neg(c))
            elif arm.type == "other_arm":
                if none_before is not FALSE:
                    alts.append((none_before, self._guarded(named(arm)[-1], env, ctx)))
                none_before = FALSE
            if none_before is FALSE:
                break
        if none_before is not FALSE:
            alts.append((none_before, BOTTOM))
        return mk_cases(alts)

    def let_env(self, node, env, ctx):
        names_d = {}
        e2 = env.bind(names_d)
        for d in node.children_by_field_name("definitions"):
            if d.type in ("operator_definition", "function_definition", "local_definition"):
                from tla_front import _def_from
                dd = _def_from(d)
                if dd.kind == "fn":
                    # Bind recursive functions to a lazily evaluated function, so that the
                    # recursive references in the body see it.
                    bnames, bsets = self._bounds(dd.bounds, e2, ctx)
                    if all(is_concrete(x) for x in bsets):
                        names_d[dd.name] = LazyFn(self, dd, None, ctx, bnames, bsets, env=e2)
                    else:
                        names_d[dd.name] = Thunk(_FnDefNode(dd), e2, ctx)
                elif dd.params:
                    names_d[dd.name] = Closure(dd.params, dd.body, e2, dd.name)
                else:
                    names_d[dd.name] = Thunk(dd.body, e2, ctx)
            elif d.type == "recursive_declaration":
                for od in d.named_children:
                    if od.type == "operator_declaration":
                        self.recursive.add(text(od.named_children[0]))
                continue
            else:
                raise Unsupported(f"LET {d.type}")
        return e2

    def ev_let_in(self, node, env, ctx):
        e2 = self.let_env(node, env, ctx)
        return self.ev(node.child_by_field_name("expression"), e2, ctx)

    def ev__fn_def(self, fd: "_FnDefNode", env, ctx):
        d = fd.d
        names_list, sets = self._bounds(d.bounds, env, ctx)
        sym = tuple(i for i, x in enumerate(sets) if not is_concrete(x))
        if sym:  # a domain that depends on the state: one function per concrete domain
            return self.lift_sym(lambda *ss: self._fn_def_on(d, names_list, list(ss), env, ctx), *sets, keys=sym)
        return self._fn_def_on(d, names_list, sets, env, ctx)

    def _fn_def_on(self, d, names_list, sets, env, ctx):
        names_d = {}
        e2 = env.bind(names_d)
        lazy = LazyFn(self, d, None, ctx, names_list, sets, env=e2)
        names_d[d.name] = lazy  # recursive references
        if all(isinstance(s, frozenset) for s in sets) and _product_size(sets) <= 100000:
            return lazy.materialize()
        return lazy

    def ev_conj_list(self, node, env, ctx):
        parts = []
        for item in named(node):
            f = self.to_formula(self.ev(named(item)[-1], env, ctx))
            if f is FALSE:
                return False
            parts.append(f)
        return mk_bool(conj(*parts))

    def ev_disj_list(self, node, env, ctx):
        parts = []
        for item in named(node):
            f = self.to_formula(self.ev(named(item)[-1], env, ctx))
            if f is TRUE:
                return True
            parts.append(f)
        return mk_bool(disj(*parts))

    def ev_finite_set_literal(self, node, env, ctx):
        vals = [self.ev(c, env, ctx) for c in named(node)]
        if all(is_concrete(v) for v in vals):
            return frozenset(vals)
        mem = {}
        for v in vals:
            for c, x in self.concretize(v):
                mem[x] = disj(mem.get(x, FALSE), c)
        return mk_set(mem)

    def ev_tuple_literal(self, node, env, ctx):
        vals = [self.ev(c, env, ctx) for c in named(node)
                if c.type not in ("langle_bracket", "rangle_bracket")]
        return mk_fn({i + 1: v for i, v in enumerate(vals)})

    def ev_record_literal(self, node, env, ctx):
        kids = [c for c in named(node) if c.type != "all_map_to"]
        out = {}
        for i in range(0, len(kids), 2):
            out[text(kids[i])] = self.ev(kids[i + 1], env, ctx)
        return mk_fn(out)

    def ev_set_of_records(self, node, env, ctx):
        kids = named(node)
        fields = [(text(kids[i]), self.force(self.ev(kids[i + 1], env, ctx))) for i in range(0, len(kids), 2)]
        if all(isinstance(s, frozenset) for _, s in fields):
            if _product_size([s for _, s in fields]) <= 200000:
                return frozenset(Fn(dict(zip([f for f, _ in fields], combo)))
                                 for combo in itertools.product(*(sorted_values(s) for _, s in fields)))
        names = [f for f, _ in fields]
        return LazySet("[record set]", lambda r: isinstance(r, Fn) and set(r.keys()) == set(names)
                       and all(_lazy_mem(s, r[f]) for f, s in fields))

    def ev_set_of_functions(self, node, env, ctx):
        kids = [c for c in named(node) if c.type != "maps_to"]
        dom = self.force(self.ev(kids[0], env, ctx))
        rng = self.force(self.ev(kids[1], env, ctx))
        if not is_concrete(dom) or not is_concrete(rng):
            return self.lift(lambda d, r: _fn_set(d, r), dom, rng)
        return _fn_set(dom, rng)

    def ev_record_value(self, node, env, ctx):
        kids = named(node)
        r = self.ev(kids[0], env, ctx)
        return self.apply(r, text(kids[-1]))

    def ev_function_evaluation(self, node, env, ctx):
        kids = named(node)
        f = self.ev(kids[0], env, ctx)
        args = [self.ev(k, env, ctx) for k in kids[1:]]
        if len(args) == 1:
            return self.apply(f, args[0])
        return self.apply(f, mk_fn({i + 1: a for i, a in enumerate(args)}))

    def ev_function_literal(self, node, env, ctx):
        bounds = [c for c in node.named_children if c.type == "quantifier_bound"]
        body = named(node)[-1]
        names, sets = self._bounds(bounds, env, ctx)
        sets = [self.force(s) for s in sets]
        if not all(isinstance(s, frozenset) for s in sets):
            if any(isinstance(s, LazySet) for s in sets):
                return LazyFn(self, None, None, ctx, names, sets, env=env, body=body)
            # A function whose domain depends on the state: split the domain.
            return self.lift_sym(lambda *ss: self._fn_literal(names, list(ss), body, env, ctx), *sets,
                                 keys=tuple(range(len(sets))))
        return self._fn_literal(names, sets, body, env, ctx)

    def _fn_literal(self, names, sets, body, env, ctx):
        out = {}
        for combo in itertools.product(*(sorted_values(s) for s in sets)):
            d = {}
            for n, x in zip(names, combo):
                self._bind(n, x, d)
            key = combo[0] if len(combo) == 1 else Fn.tuple(combo)
            out[key] = self._guarded(body, env.bind(d), ctx)
        return mk_fn(out)

    def ev_set_filter(self, node, env, ctx):
        b = next(c for c in node.named_children if c.type == "quantifier_bound")
        body = node.child_by_field_name("filter") or named(node)[-1]
        mem = {}
        for c, e2 in self.enumerate_bindings([b], env, ctx):
            name = next(iter(e2.names))
            ids = [x for x in b.named_children if x.type in ("identifier", "tuple_of_identifiers")]
            if ids and ids[0].type == "tuple_of_identifiers":
                x = Fn.tuple([e2.names[n] for n in (text(i) for i in ids[0].named_children if i.type == "identifier")])
            else:
                x = e2.names[name]
            f = conj(c, self.to_formula(self._guarded(body, e2, ctx)))
            mem[x] = disj(mem.get(x, FALSE), f)
        return mk_set(mem)

    def ev_set_map(self, node, env, ctx):
        bounds = [c for c in node.named_children if c.type == "quantifier_bound"]
        body = named(node)[0]
        mem = {}
        for c, e2 in self.enumerate_bindings(bounds, env, ctx):
            v = self._guarded(body, e2, ctx)
            for c2, x in self.concretize(v):
                cc = conj(c, c2)
                if cc is not FALSE:
                    mem[x] = disj(mem.get(x, FALSE), cc)
        return mk_set(mem)

    def ev_except(self, node, env, ctx):
        base = self.ev(node.child_by_field_name("expr_to_update"), env, ctx)
        for upd in [c for c in named(node) if c.type == "except_update"]:
            spec = next(c for c in named(upd) if c.type == "except_update_specifier")
            path = []
            for comp in named(spec):
                if comp.type == "except_update_fn_appl":
                    ks = [self.ev(k, env, ctx) for k in named(comp)]
                    path.append(ks[0] if len(ks) == 1 else mk_fn({i + 1: k for i, k in enumerate(ks)}))
                else:
                    path.append(text(named(comp)[-1]))
            new_node = named(upd)[-1]
            base = self._except(base, path, new_node, env, ctx)
        return base

    def _except(self, f, path, new_node, env, ctx):
        f = self.unenum(self.force(f))
        if isinstance(f, Cases):
            return mk_cases([(c, self._except(x, path, new_node, env, ctx)) for c, x in f.alts])
        if f is BOTTOM:
            return BOTTOM
        if not isinstance(f, (Fn, SFn)):
            raise EvalError("EXCEPT on a non-function")
        m = dict(f._map if isinstance(f, Fn) else f.map)
        k = path[0]
        if is_concrete(k):
            keys = [(TRUE, k)] if k in m else []
        else:
            keys = [(self.equal(k, key), key) for key in m]
        for c, key in keys:
            if c is FALSE:
                continue
            old = m[key]
            if len(path) == 1:
                new = self._guarded(new_node, env.bind({"@": old}), ctx)
            else:
                new = self._except(old, path[1:], new_node, env, ctx)
            m[key] = new if c is TRUE else mk_cases([(c, new), (neg(c), old)])
        return mk_fn(m)

    # ---- temporal operators never reach value evaluation
    def ev_step_expr_or_stutter(self, node, env, ctx):
        raise Unsupported("[A]_v outside an action")

    ev_step_expr_no_stutter = ev_step_expr_or_stutter

    def ev_fairness(self, node, env, ctx):
        raise Unsupported("fairness outside a specification")

    # ---------------------------------------------------------------- UNCHANGED / ENABLED

    def unchanged_vars(self, node, env) -> list[str] | None:
        """The state variables of `UNCHANGED e` when e is a (nested) tuple of variables."""
        t = node.type
        if t == "parentheses":
            return self.unchanged_vars(named(node)[0], env)
        if t == "tuple_literal":
            out = []
            for c in named(node):
                if c.type in ("langle_bracket", "rangle_bracket"):
                    continue
                vs = self.unchanged_vars(c, env)
                if vs is None:
                    return None
                out += vs
            return out
        if t == "identifier_ref":
            kind, *rest = self.resolve(text(node), env)
            if kind == "var":
                return [rest[0]]
            if kind == "def" and not rest[0].params and rest[0].kind == "op":
                return self.unchanged_vars(rest[0].body, rest[1].env)
            if kind == "subst":
                return self.unchanged_vars(rest[0], rest[1])
            if kind == "local" and isinstance(rest[0], Thunk):
                return self.unchanged_vars(rest[0].node, rest[0].env)
        return None

    def unchanged_formula(self, node, env, ctx) -> Formula:
        vs = self.unchanged_vars(node, env)
        if vs is not None:
            return conj(*(self.equal(self.read_var(v, Ctx(ctx.assigned, True)), self.read_var(v, Ctx()))
                          for v in vs))
        now = self.ev(node, env, Ctx(ctx.assigned))
        nxt = self.ev(node, env, Ctx(ctx.assigned, True))
        return self.equal(now, nxt)

    def enabled(self, node, env, ctx) -> Formula:
        branches = self.ev_action(node, env, [Branch(TRUE, {})], Ctx())
        return disj(*(b.guard for b in branches))

    # ---------------------------------------------------------------- actions

    def ev_action(self, node, env, branches: list, ctx: Ctx) -> list:
        """Ground an action: returns the branches after `node`. In `ctx.init` mode, unprimed
        variables are assigned (for Init)."""
        out = []
        for br in branches:
            out.extend(self._act(node, env, br, ctx))
            if len(out) > BRANCH_LIMIT:
                raise TooBig("too many branches")
        return out

    def _bctx(self, br: Branch, ctx: Ctx) -> Ctx:
        return Ctx(br.assigned, False, ctx.init)

    def _with_guard(self, br: Branch, f: Formula) -> list:
        g = conj(br.guard, f)
        return [] if g is FALSE else [Branch(g, br.assigned, br.label)]

    def _act(self, node, env, br: Branch, ctx: Ctx) -> list:
        t = node.type
        if t == "parentheses":
            return self._act(named(node)[0], env, br, ctx)
        if t == "label":
            return self._act(named(node)[-1], env, br, ctx)
        if t == "conj_list":
            bs = [br]
            for item in named(node):
                bs = self.ev_action(named(item)[-1], env, bs, ctx)
                if not bs:
                    break
            return bs
        if t == "disj_list":
            out = []
            for item in named(node):
                out.extend(self._act(named(item)[-1], env, br, ctx))
            return out
        if t == "boolean":
            return [br] if text(node) == "TRUE" else []
        if t == "bound_infix_op":
            st = node.child_by_field_name("symbol").type
            lhs, rhs = node.child_by_field_name("lhs"), node.child_by_field_name("rhs")
            if st == "land":
                return self.ev_action(rhs, env, self._act(lhs, env, br, ctx), ctx)
            if st == "lor":
                return self._act(lhs, env, br, ctx) + self._act(rhs, env, br, ctx)
            if st == "implies":
                a = self.to_formula(self.ev(lhs, env, self._bctx(br, ctx)))
                return self._with_guard(br, neg(a)) + \
                    [b for b2 in self._with_guard(br, a) for b in self._act(rhs, env, b2, ctx)]
            if st in ("eq", "in"):
                target = self._assign_target(lhs, env, br, ctx)
                if target is not None:
                    return self._assign(target, st, rhs, env, br, ctx)
        if t == "bounded_quantification" and node.child_by_field_name("quantifier").type == "exists":
            bounds = [c for c in node.named_children if c.type == "quantifier_bound"]
            out = []
            for c, e2 in self.enumerate_bindings(bounds, env, self._bctx(br, ctx)):
                for b2 in self._with_guard(br, c):
                    out.extend(self._act(named(node)[-1], e2, b2, ctx))
            return out
        if t == "if_then_else":
            c = self.to_formula(self.ev(node.child_by_field_name("if"), env, self._bctx(br, ctx)))
            out = []
            for b2 in self._with_guard(br, c):
                out.extend(self._act(node.child_by_field_name("then"), env, b2, ctx))
            for b2 in self._with_guard(br, neg(c)):
                out.extend(self._act(node.child_by_field_name("else"), env, b2, ctx))
            return out
        if t == "case":
            out, none_before = [], TRUE
            for arm in named(node):
                if arm.type == "case_arm":
                    parts = [c for c in named(arm) if c.type != "case_arrow"]
                    c = self.to_formula(self.ev(parts[0], env, self._bctx(br, ctx)))
                    for b2 in self._with_guard(br, conj(none_before, c)):
                        out.extend(self._act(parts[-1], env, b2, ctx))
                    none_before = conj(none_before, neg(c))
                elif arm.type == "other_arm":
                    for b2 in self._with_guard(br, none_before):
                        out.extend(self._act(named(arm)[-1], env, b2, ctx))
                    none_before = FALSE
            return out
        if t == "let_in":
            e2 = self.let_env(node, env, self._bctx(br, ctx))
            return self._act(node.child_by_field_name("expression"), e2, br, ctx)
        if t == "bound_prefix_op":
            st = node.child_by_field_name("symbol").type
            if st == "unchanged":
                return self._unchanged(node.child_by_field_name("rhs"), env, br, ctx)
        if t == "step_expr_or_stutter":
            kids = named(node)
            return self._act(kids[0], env, br, ctx) + self._unchanged(kids[-1], env, br, ctx)
        if t == "step_expr_no_stutter":
            kids = [c for c in named(node) if c.type not in ("langle_bracket", "rangle_bracket_sub")]
            out = []
            for b2 in self._act(kids[0], env, br, ctx):
                f = neg(self.unchanged_formula(kids[-1], env, self._bctx(b2, ctx)))
                out.extend(self._with_guard(b2, f))
            return out
        if t in ("identifier_ref", "bound_op", "prefixed_op"):
            inl = self._inline(node, env, br, ctx)
            if inl is not None:
                return inl
        # A plain formula (state predicate, or a constraint on already assigned primes).
        f = self.to_formula(self.ev(node, env, self._bctx(br, ctx)))
        return self._with_guard(br, f)

    def _inline(self, node, env, br, ctx):
        """Inline an operator application in action position (None if it is not an operator
        with a body, e.g. a variable or a constant)."""
        bctx = self._bctx(br, ctx)
        if node.type == "prefixed_op":
            scope, env2 = self.instance_target(node, env, bctx)
            inner = named(node)[-1]
            if inner.type == "bound_op":
                name = text(inner.child_by_field_name("name"))
                args = [self._arg(c, env, bctx) for c in self._op_args(inner)]
            else:
                name, args = text(inner), []
        else:
            if node.type == "bound_op":
                name = text(node.child_by_field_name("name"))
                args = [self._arg(c, env, bctx) for c in self._op_args(node)]
            else:
                name, args = text(node), []
            env2 = env
        kind, *rest = self.resolve(name, env2)
        if kind == "def" and rest[0].kind == "op" and len(rest[0].params) == len(args):
            d, scope = rest
            names_d = {(p if isinstance(p, str) else p[0]): a for p, a in zip(d.params, args)}
            if not br.label and d.params:
                br = Branch(br.guard, br.assigned, f"{name}({', '.join(self._arg_text(a) for a in args)})")
            elif not br.label and d.name not in ("Next",):
                br = Branch(br.guard, br.assigned, name) if _looks_like_action(d.name) else br
            return self._act(d.body, Env(names_d, scope.env, scope), br, ctx)
        if kind == "local":
            v = rest[0]
            if isinstance(v, Thunk) and not args:
                return self._act(v.node, v.env, br, ctx)
            if isinstance(v, Closure) and len(v.params) == len(args):
                names_d = {(p if isinstance(p, str) else p[0]): a for p, a in zip(v.params, args)}
                return self._act(v.body, v.env.bind(names_d), br, ctx)
        if kind == "subst" and not args:
            return self._act(rest[0], rest[1], br, ctx)
        return None

    def _arg_text(self, a) -> str:
        try:
            v = self.force(a)
        except EvalError:
            return "?"
        return show(v) if is_concrete(v) else "?"

    def _assign_target(self, lhs, env, br, ctx) -> str | None:
        """The variable assigned by `lhs = e` / `lhs \\in S`, if this is an assignment."""
        while lhs.type == "identifier_ref":  # an operator parameter standing for x'
            found, v = env.find_local(text(lhs))
            if not found or not isinstance(v, Thunk):
                break
            lhs, env = v.node, v.env
        while lhs.type == "parentheses":
            lhs = named(lhs)[0]
        if ctx.init:
            if lhs.type != "identifier_ref":
                return None
            name = text(lhs)
        else:
            if lhs.type != "bound_postfix_op" or lhs.child_by_field_name("symbol").type != "prime":
                return None
            inner = lhs.child_by_field_name("lhs")
            while inner.type == "identifier_ref":  # q' for an operator parameter q standing for x
                found, v = env.find_local(text(inner))
                if not found or not isinstance(v, Thunk):
                    break
                inner, env = v.node, v.env
            if inner.type != "identifier_ref":
                return None
            name = text(inner)
        kind, *rest = self.resolve(name, env)
        while kind == "subst":
            node, senv = rest
            if node.type != "identifier_ref":
                return None
            kind, *rest = self.resolve(text(node), senv)
        if kind != "var":
            return None
        var = rest[0]
        return None if var in br.assigned else var

    def _assign(self, var, op, rhs, env, br, ctx) -> list:
        bctx = self._bctx(br, ctx)
        v = self.ev(rhs, env, bctx)
        if op == "eq":
            if v is BOTTOM:
                return []
            return [Branch(br.guard, {**br.assigned, var: v}, br.label)]
        out = []
        for c, x in self.enumerate_set(self.force(v)):
            g = conj(br.guard, c)
            if g is not FALSE:
                out.append(Branch(g, {**br.assigned, var: x}, br.label))
        return out

    def _unchanged(self, node, env, br, ctx) -> list:
        vs = self.unchanged_vars(node, env)
        if vs is None:
            f = self.unchanged_formula(node, env, self._bctx(br, ctx))
            return self._with_guard(br, f)
        assigned = dict(br.assigned)
        guard = br.guard
        for v in vs:
            if v in assigned:
                guard = conj(guard, self.equal(assigned[v], self.read_var(v, Ctx())))
            else:
                assigned[v] = self.read_var(v, Ctx())
        return [] if guard is FALSE else [Branch(guard, assigned, br.label)]


def _looks_like_action(name: str) -> bool:
    return name not in ("Next", "Spec", "Init") and not name.startswith("vars")


class _FnDefNode:
    """Wraps a LET-defined recursive function so `ev` can dispatch on it."""
    type = "_fn_def"

    def __init__(self, d: Def):
        self.d = d


class LazyFn:
    """A function given by its definition, evaluated on demand (recursive and infinite-domain
    functions)."""

    def __init__(self, ev: Evaluator, d: Def | None, scope: Scope | None, ctx: Ctx, names, sets,
                 env: Env | None = None, body=None):
        self.ev, self.d, self.ctx, self.names, self.sets = ev, d, ctx, names, sets
        self.env = env if env is not None else scope.env
        self.body = body if body is not None else d.body
        self.memo = {}

    def in_domain(self, k) -> bool:
        if len(self.names) == 1:
            return _lazy_mem(self.sets[0], k)
        ks = _seq(k)
        return len(ks) == len(self.sets) and all(_lazy_mem(s, x) for s, x in zip(self.sets, ks))

    def apply(self, k):
        try:
            hash(k)
        except TypeError:
            raise EvalError("unhashable function argument")
        if k in self.memo:
            return self.memo[k]
        if not self.in_domain(k):
            raise EvalError(f"function applied outside its domain: {show(k)}")
        d = {}
        if len(self.names) == 1:
            self.ev._bind(self.names[0], k, d)
        else:
            for n, x in zip(self.names, _seq(k)):
                self.ev._bind(n, x, d)
        v = self.ev.ev(self.body, self.env.bind(d), self.ctx)
        self.memo[k] = v
        return v

    def materialize(self):
        out = {}
        for combo in itertools.product(*(sorted_values(s) for s in self.sets)):
            key = combo[0] if len(combo) == 1 else Fn.tuple(combo)
            try:
                out[key] = self.apply(key)
            except Unsupported:
                raise
            except EvalError:
                out[key] = BOTTOM
        return mk_fn(out)

    def domain_set(self):
        if len(self.sets) == 1:
            return self.sets[0]
        raise Unsupported("DOMAIN of a multi-argument lazy function")


# --------------------------------------------------------------------------------------------
# Concrete helpers and builtins
# --------------------------------------------------------------------------------------------

def values_equal(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, LazyFn) or isinstance(b, LazyFn):
        raise Unsupported("comparing infinite functions")
    return type(a) == type(b) and a == b or (is_int(a) and is_int(b) and a == b)


def _same_kind(a, b) -> bool:
    return isinstance(a, bool) == isinstance(b, bool)


def _int(x) -> int:
    if not is_int(x):
        raise EvalError(f"expected an integer, got {show(x)}")
    return x


def _cmp(st, x, y) -> bool:
    x, y = _int(x), _int(y)
    return {"lt": x < y, "leq": x <= y, "gt": x > y, "geq": x >= y}[st]


def _floordiv(x, y):
    if y == 0:
        raise EvalError("division by zero")
    return x // y


def _mod(x, y):
    if y <= 0:
        raise EvalError("modulus must be positive")
    return x % y


def _finite(s) -> frozenset:
    if isinstance(s, frozenset):
        return s
    if isinstance(s, LazySet) and s.elems is not None:
        return s.elems()
    raise EvalError(f"expected a finite set, got {s!r}")


def _seq(x) -> list:
    if isinstance(x, Fn):
        return x.seq()
    raise EvalError(f"expected a sequence, got {show(x)}")


def _lazy_mem(s, x) -> bool:
    if isinstance(s, frozenset):
        return x in s and any(values_equal(x, y) for y in (x,))
    if isinstance(s, LazySet):
        return s.member(x)
    raise EvalError("membership in a non-set")


def _powerset(s):
    s = sorted_values(_finite(s))
    if len(s) > 16:
        return LazySet("SUBSET", lambda x: isinstance(x, frozenset) and x <= frozenset(s))
    return frozenset(frozenset(c) for r in range(len(s) + 1) for c in itertools.combinations(s, r))


def _fn_set(dom, rng):
    elems = None
    if isinstance(dom, frozenset) and isinstance(rng, frozenset):
        ds = sorted_values(dom)
        rs = sorted_values(rng)
        size = len(rng) ** len(ds)
        if size <= 100000:
            return frozenset(Fn(dict(zip(ds, combo))) for combo in itertools.product(rs, repeat=len(ds)))
        if size <= 2_000_000:
            elems = lambda: frozenset(Fn(dict(zip(ds, combo))) for combo in itertools.product(rs, repeat=len(ds)))
    return LazySet("[S -> T]", lambda f: isinstance(f, Fn) and f.domain() == _finite(dom)
                   and all(_lazy_mem(rng, v) for v in f.values()), elems)


def _compose(f, g):
    m = dict(g.items())
    m.update(f.items())
    return Fn(m)


def _product_size(lists) -> int:
    n = 1
    for x in lists:
        n *= len(x)
    return n


NAT = LazySet("Nat", lambda x: is_int(x) and x >= 0)
INT = LazySet("Int", is_int)
STRING = LazySet("STRING", lambda x: isinstance(x, str))


def _seq_set(s):
    return LazySet("Seq(S)", lambda x: isinstance(x, Fn) and x.is_seq() and all(_lazy_mem(s, v) for v in x.seq()))


def _max(s):
    s = _finite(s)
    if not s:
        raise EvalError("Max of empty set")
    return max(_int(x) for x in s)


def _min(s):
    s = _finite(s)
    if not s:
        raise EvalError("Min of empty set")
    return min(_int(x) for x in s)


def _subseq(s, m, n):
    xs = _seq(s)
    m, n = _int(m), _int(n)
    if m > n:
        return Fn.tuple([])
    if m < 1 or n > len(xs):
        raise EvalError("SubSeq out of range")
    return Fn.tuple(xs[m - 1:n])


def _head(s):
    xs = _seq(s)
    if not xs:
        raise EvalError("Head of empty sequence")
    return xs[0]


def _tail(s):
    xs = _seq(s)
    if not xs:
        raise EvalError("Tail of empty sequence")
    return Fn.tuple(xs[1:])


def _assert(c, msg):
    if c is not True:
        raise EvalError(f"Assert failed: {msg}")
    return True


def _permutations(s):
    xs = sorted_values(_finite(s))
    return frozenset(Fn(dict(zip(xs, p))) for p in itertools.permutations(xs))


def _bag_to_set(b):
    return frozenset(k for k, v in b.items() if v > 0)


# Builtins taking evaluated arguments (lifted over symbolic values unless noted).
_STRICT = {
    "Len": lambda s: len(_seq(s)),
    "Head": _head,
    "Tail": _tail,
    "Append": lambda s, e: Fn.tuple(_seq(s) + [e]),
    "SubSeq": _subseq,
    "Seq": _seq_set,
    "Cardinality": lambda s: len(_finite(s)),
    "IsFiniteSet": lambda s: isinstance(s, frozenset),
    "ToString": lambda v: show(v),
    "Permutations": _permutations,
    "Max": _max,
    "Min": _min,
    "Range": lambda f: frozenset(f.values()),
    "ToSet": lambda s: frozenset(_seq(s)),
    "Front": lambda s: Fn.tuple(_seq(s)[:-1]) if _seq(s) else (_ for _ in ()).throw(EvalError("Front")),
    "Last": lambda s: _seq(s)[-1] if _seq(s) else (_ for _ in ()).throw(EvalError("Last")),
    "Reverse": lambda s: Fn.tuple(list(reversed(_seq(s)))),
    "Cons": lambda e, s: Fn.tuple([e] + _seq(s)),
    "IsPrefix": lambda s, t: _seq(t)[:len(_seq(s))] == _seq(s),
    "SetToSeq": lambda s: Fn.tuple(sorted_values(_finite(s))),
    "SumSet": lambda s: sum(_int(x) for x in _finite(s)),
    "Sum": lambda s: sum(_int(x) for x in _finite(s)),
    "IsInjective": lambda f: len(set(f.values())) == len(f),
    "Assert": _assert,
    "BagToSet": _bag_to_set,
    "SetToBag": lambda s: Fn({x: 1 for x in _finite(s)}),
    "Contains": lambda s, e: e in _seq(s),
}

BUILTIN_CONSTANTS = {"EmptyBag": Fn({}), "Nat": NAT, "Int": INT}

BUILTINS = set(_STRICT) | set(BUILTIN_CONSTANTS) | {
    "Print", "PrintT", "SelectSeq", "SortSeq", "FoldSet", "FoldFunction", "FoldSeq", "MapThenFoldSet",
    "Quantify", "TLCGet", "TLCSet", "RandomElement", "Restrict", "JavaTime", "Any", "Cardinality",
    "Len", "ReduceSet", "Fold", "FoldFunctionOnSet", "SeqOf", "kSubset", "Injection", "Bijection",
    "Surjection", "Inverse", "AntiFunction",
}


def _seq_alts(ev: Evaluator, v) -> list:
    """[(condition, list of possibly symbolic elements)] for a sequence value, splitting only
    the length (never the elements)."""
    v = ev.force(v)
    if isinstance(v, Cases):
        out = []
        for c, x in v.alts:
            for c2, xs in _seq_alts(ev, x):
                cc = conj(c, c2)
                if cc is not FALSE:
                    out.append((cc, xs))
        return out
    if v is BOTTOM:
        return []
    if isinstance(v, (Fn, SFn)):
        m = v._map if isinstance(v, Fn) else v.map
        n = len(m)
        if set(m) != set(range(1, n + 1)):
            return []  # not a sequence: undefined
        return [(TRUE, [m[i] for i in range(1, n + 1)])]
    return [(c, x.seq()) for c, x in ev.concretize(v) if isinstance(x, Fn) and x.is_seq()]


def _seq_builtin(ev: Evaluator, name: str, args: list):
    """Sequence operators on sequences with symbolic elements."""
    def one(xs, rest):
        if name == "Len":
            return len(xs)
        if name == "Head":
            if not xs:
                raise EvalError("Head of empty sequence")
            return xs[0]
        if name == "Tail":
            if not xs:
                raise EvalError("Tail of empty sequence")
            return mk_fn({i + 1: x for i, x in enumerate(xs[1:])})
        if name == "Append":
            return mk_fn({i + 1: x for i, x in enumerate(xs + [rest[0]])})
        if name == "Front":
            if not xs:
                raise EvalError("Front of empty sequence")
            return mk_fn({i + 1: x for i, x in enumerate(xs[:-1])})
        if name == "Last":
            if not xs:
                raise EvalError("Last of empty sequence")
            return xs[-1]
        if name == "Reverse":
            return mk_fn({i + 1: x for i, x in enumerate(reversed(xs))})
        if name == "SubSeq":
            m, n = rest
            if m > n:
                return Fn.tuple([])
            if m < 1 or n > len(xs):
                raise EvalError("SubSeq out of range")
            return mk_fn({i + 1: x for i, x in enumerate(xs[m - 1:n])})
        raise Unsupported(name)

    if name in ("IsPrefix", "Cons", "\\o"):
        a_alts, b_alts = _seq_alts(ev, args[0]) if name != "Cons" else [(TRUE, None)], _seq_alts(ev, args[1])
        alts = []
        for c1, xs in a_alts:
            for c2, ys in b_alts:
                c = conj(c1, c2)
                if c is FALSE:
                    continue
                if name == "IsPrefix":
                    f = FALSE if len(xs) > len(ys) else conj(*(ev.equal(x, y) for x, y in zip(xs, ys)))
                    alts.append((c, mk_bool(f)))
                elif name == "Cons":
                    alts.append((c, mk_fn({i + 1: x for i, x in enumerate([args[0]] + ys)})))
                else:
                    alts.append((c, mk_fn({i + 1: x for i, x in enumerate(xs + ys)})))
        return mk_cases(alts) if alts else BOTTOM
    rest = args[1:]
    if name == "SubSeq" and not all(is_int(r) for r in rest):
        return ev.lift(_STRICT[name], *args)
    alts = []
    for c, xs in _seq_alts(ev, args[0]):
        alts.append((c, ev._try(one, xs, rest)))
    return mk_cases(alts) if alts else BOTTOM


_SEQ_SYMBOLIC = {"Len", "Head", "Tail", "Append", "Front", "Last", "Reverse", "SubSeq", "IsPrefix", "Cons"}


def call_builtin(ev: Evaluator, name: str, args: list, ctx: Ctx, node, env):
    if name in _SEQ_SYMBOLIC and not all(is_concrete(a) for a in args):
        return _seq_builtin(ev, name, args)
    if name in _STRICT:
        fn = _STRICT[name]
        if name == "Cardinality" and len(args) == 1 and isinstance(args[0], SSet):
            return _cardinality(ev, args[0])
        if name == "Len" and len(args) == 1 and not is_concrete(args[0]):
            return ev.lift(fn, *args)
        return ev.lift(fn, *args)
    if name == "Print":
        return args[1]
    if name == "PrintT":
        return True
    if name == "SelectSeq":
        s, test = args
        return ev.lift_sym(lambda xs: _select_seq(ev, xs, test, ctx), s, keys=(0,))
    if name == "SortSeq":
        s, op = args
        return ev.lift_sym(lambda xs: _sort_seq(ev, xs, op, ctx), s, keys=(0,))
    if name in ("FoldSet", "FoldFunction", "FoldSeq", "MapThenFoldSet", "Fold", "FoldFunctionOnSet",
                "ReduceSet"):
        return _fold(ev, name, args, ctx)
    if name == "Quantify":
        s, p = args
        return ev.lift_sym(lambda ss: sum(1 for x in sorted_values(_finite(ss))
                                          if ev.call_operator(p, [x], ctx) is True), s, keys=(0,))
    if name == "Restrict":
        return ev.lift(lambda f, s: Fn({k: v for k, v in f.items() if k in s}), *args)
    if name == "Inverse":
        return ev.lift(lambda f, s, t: Fn({y: next(x for x in sorted_values(_finite(s)) if f[x] == y)
                                           for y in _finite(t) if y in set(f.values())}), *args)
    if name == "kSubset":
        return ev.lift(lambda k, s: frozenset(frozenset(c) for c in itertools.combinations(
            sorted_values(_finite(s)), _int(k))), *args)
    if name == "SeqOf":
        return ev.lift(lambda s, n: frozenset(Fn.tuple(c) for k in range(_int(n) + 1)
                                              for c in itertools.product(sorted_values(_finite(s)), repeat=k)), *args)
    raise Unsupported(f"builtin {name}")


def _cardinality(ev, s: SSet):
    total = Lin()
    for k, f in s.mem.items():
        if f is TRUE:
            total = total + 1
            continue
        if isinstance(f, type(atom(Lin.var("x") - 1, "=="))) and f.op == "==" and len(f.lin.terms) == 1:
            (slot, c), = f.lin.terms.items()
            vals = ev.slot_values.get(slot)
            if c == 1 and vals is not None and set(vals) <= {0, 1}:
                if f.lin.const == -1:
                    total = total + Lin.var(slot)
                    continue
                if f.lin.const == 0:
                    total = total + 1 - Lin.var(slot)
                    continue
        return ev.lift(lambda x: len(x), s)
    return mk_int(total)


def _select_seq(ev, xs, test, ctx):
    out = []
    for x in _seq(xs):
        r = ev.call_operator(test, [x], ctx)
        if not isinstance(r, bool):
            raise Unsupported("SelectSeq with a state-dependent test")
        if r:
            out.append(x)
    return Fn.tuple(out)


def _sort_seq(ev, xs, op, ctx):
    import functools

    def cmp(a, b):
        r = ev.call_operator(op, [a, b], ctx)
        if not isinstance(r, bool):
            raise Unsupported("SortSeq with a state-dependent order")
        return -1 if r else 1
    return Fn.tuple(sorted(_seq(xs), key=functools.cmp_to_key(cmp)))


def _fold(ev, name, args, ctx):
    def run(*cargs):
        if name in ("FoldSet", "ReduceSet"):
            op, base, s = cargs
            acc = base
            for x in sorted_values(_finite(s)):
                acc = ev.call_operator(op, [x, acc], ctx)
            return acc
        if name == "FoldSeq":
            op, base, s = cargs
            acc = base
            for x in _seq(s):
                acc = ev.call_operator(op, [x, acc], ctx)
            return acc
        if name == "FoldFunction":
            op, base, f = cargs
            acc = base
            for k in sorted_values(f.domain()):
                acc = ev.call_operator(op, [f[k], acc], ctx)
            return acc
        if name == "FoldFunctionOnSet":
            op, base, f, s = cargs
            acc = base
            for k in sorted_values(_finite(s)):
                acc = ev.call_operator(op, [f[k], acc], ctx)
            return acc
        if name == "MapThenFoldSet":
            op, base, f, choose, s = cargs
            acc = base
            for x in sorted_values(_finite(s)):
                acc = ev.call_operator(op, [ev.call_operator(f, [x], ctx), acc], ctx)
            return acc
        raise Unsupported(f"builtin {name}")
    keys = tuple(i for i, a in enumerate(args) if not isinstance(a, (Closure, Builtin, tuple)))
    return ev.lift_sym(run, *args, keys=keys)
