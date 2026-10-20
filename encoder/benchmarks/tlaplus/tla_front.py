"""Load TLA+ modules (via tree-sitter) and TLC model configurations.

Only what the evaluator needs is extracted: constants, variables, definitions (operators,
functions, named instances) and the EXTENDS / INSTANCE structure. Proofs, theorems and
assumptions are ignored. Standard and community modules are not parsed; their operators are
built into the evaluator (`tla_eval.BUILTINS`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter as ts
import tree_sitter_tlaplus

from tla_values import EvalError, Fn, ModelValue, _ValueParser

_LANG = ts.Language(tree_sitter_tlaplus.language())
_PARSER = ts.Parser(_LANG)

# Modules whose operators are built in (or that only matter for proofs).
BUILTIN_MODULES = {
    "Naturals", "Integers", "Reals", "Sequences", "FiniteSets", "TLC", "Bags", "TLCExt",
    "TLAPS", "NaturalsInduction", "WellFoundedInduction", "FiniteSetTheorems", "SequenceTheorems",
    "FunctionTheorems", "BagsTheorems", "SequencesExtTheorems", "FiniteSetsExtTheorems",
    "Functions", "SequencesExt", "FiniteSetsExt", "Folds", "Randomization", "IOUtils", "CSV",
    "Json", "Toolbox", "Apalache", "Variants", "DyadicRationals", "Relation", "Graphs",
    "UndirectedGraphs", "Bitwise", "Combinatorics", "BagsExt", "Statistics", "VectorClocks",
    "RealTime", "DifferentialEquations",
}


def text(node: ts.Node) -> str:
    return node.text.decode()


def named(node: ts.Node) -> list[ts.Node]:
    return [c for c in node.named_children if c.type not in ("comment", "block_comment")]


@dataclass
class Def:
    name: str
    params: list            # names, or (name, arity) for operator parameters
    body: ts.Node | None
    kind: str               # 'op' | 'fn' | 'inst'
    node: ts.Node
    bounds: list = field(default_factory=list)  # 'fn': quantifier_bound nodes


@dataclass
class InstanceDecl:
    module: str
    subst: dict             # name -> expression node


@dataclass
class Module:
    name: str
    path: Path | None
    constants: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    defs: dict = field(default_factory=dict)          # own definitions
    instances: list = field(default_factory=list)     # unnamed INSTANCEs
    builtins: set = field(default_factory=set)        # builtin modules in scope
    recursive: set = field(default_factory=set)       # RECURSIVE operator names
    # Filled by the loader: everything visible (own + extended), name -> (Def, owner) where
    # owner is None for definitions evaluated in this module's scope, or an InstanceDecl.
    visible: dict = field(default_factory=dict)


def _params(node: ts.Node) -> list:
    out = []
    for i, c in enumerate(node.children):
        if node.field_name_for_child(i) != "parameter":
            continue
        if c.type == "identifier":
            out.append(text(c))
        elif c.type == "operator_declaration":
            name = next(n for n in c.named_children if n.type in ("identifier",) or n.type.endswith("op_symbol"))
            arity = sum(1 for n in c.named_children if n.type == "placeholder")
            out.append((text(name), arity))
    return out


def _def_from(node: ts.Node) -> Def | None:
    t = node.type
    if t == "local_definition":
        inner = named(node)
        return _def_from(inner[0]) if inner else None
    if t == "operator_definition":
        name = node.child_by_field_name("name")
        body = node.child_by_field_name("definition")
        params = _params(node)
        if name is None:  # infix / prefix / postfix operator definitions
            sym = next((c for c in node.named_children if c.type.endswith("op_symbol")), None)
            if sym is None:
                return None
            params = [text(c) for c in node.named_children if c.type == "identifier"]
            return Def(text(sym), params, body, "op", node)
        return Def(text(name), params, body, "op", node)
    if t == "function_definition":
        name = node.child_by_field_name("name")
        body = node.child_by_field_name("definition")
        bounds = [c for c in node.named_children if c.type == "quantifier_bound"]
        return Def(text(name), [], body, "fn", node, bounds)
    if t == "module_definition":
        name = node.child_by_field_name("name")
        body = node.child_by_field_name("definition")
        return Def(text(name), _params(node), body, "inst", node)
    return None


def parse_instance(node: ts.Node) -> InstanceDecl:
    mod = None
    subst = {}
    for c in named(node):
        if c.type == "identifier_ref" and mod is None:
            mod = text(c)
        elif c.type == "substitution":
            parts = named(c)
            subst[text(parts[0])] = parts[-1]
    return InstanceDecl(mod, subst)


def _parse_module_node(mnode: ts.Node, path: Path | None) -> tuple[Module, dict]:
    name = text(mnode.child_by_field_name("name"))
    m = Module(name, path)
    submodules = {}
    for c in named(mnode):
        t = c.type
        if t == "extends":
            m.extends = [text(x) for x in named(c) if x.type == "identifier_ref"]
        elif t == "constant_declaration":
            for x in named(c):
                if x.type == "identifier":
                    m.constants.append(text(x))
                elif x.type == "operator_declaration":
                    m.constants.append(text(x.named_children[0]))
        elif t == "variable_declaration":
            m.variables += [text(x) for x in named(c) if x.type == "identifier"]
        elif t == "instance":
            m.instances.append(parse_instance(c))
        elif t == "local_definition" and named(c) and named(c)[0].type == "instance":
            m.instances.append(parse_instance(named(c)[0]))
        elif t == "recursive_declaration":
            for od in c.named_children:
                if od.type == "operator_declaration":
                    m.recursive.add(text(od.named_children[0]))
        elif t == "module":
            sub, more = _parse_module_node(c, path)
            submodules[sub.name] = sub
            submodules.update(more)
        else:
            d = _def_from(c)
            if d is not None:
                m.defs[d.name] = d
                if d.kind == "op" and d.params and d.body is not None and _has_local_recursion(d.body):
                    m.recursive.add(d.name)
    return m, submodules


def _has_local_recursion(node) -> bool:
    """Does the body define a recursive function or operator in a LET? (Such operators are
    evaluated on concrete arguments, like RECURSIVE ones.)"""
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in ("function_definition", "recursive_declaration"):
            return True
        stack.extend(n.named_children)
    return False


class Loader:
    """Finds and parses the modules of one specification directory."""

    def __init__(self, spec_dir: Path):
        self.spec_dir = spec_dir
        self.modules: dict[str, Module] = {}
        self._trees = []  # keep the trees alive

    def _find(self, name: str) -> Path | None:
        p = self.spec_dir / f"{name}.tla"
        if p.exists():
            return p
        hits = sorted(self.spec_dir.rglob(f"{name}.tla"))
        return hits[0] if hits else None

    def load(self, name: str) -> Module:
        if name in self.modules:
            return self.modules[name]
        if name in BUILTIN_MODULES:
            m = Module(name, None)
            m.builtins.add(name)
            self.modules[name] = m
            return m
        path = self._find(name)
        if path is None:
            raise EvalError(f"module {name} not found")
        src = path.read_bytes()
        tree = _PARSER.parse(src)
        self._trees.append(tree)
        mnode = next((c for c in tree.root_node.named_children if c.type == "module"), None)
        if mnode is None:
            raise EvalError(f"no module in {path}")
        m, subs = _parse_module_node(mnode, path)
        self.modules[name] = m
        for sname, sub in subs.items():
            self.modules.setdefault(sname, sub)
            self._link(sub)
        self._link(m)
        return m

    def _link(self, m: Module):
        if m.visible:
            return
        for e in m.extends:
            em = self.load(e)
            self._link(em)
            m.builtins |= em.builtins
            m.recursive |= em.recursive
            for c in em.constants:
                if c not in m.constants:
                    m.constants.append(c)
            for v in em.variables:
                if v not in m.variables:
                    m.variables.append(v)
            m.visible.update(em.visible)
        for inst in m.instances:
            im = self.load(inst.module)
            self._link(im)
            m.builtins |= im.builtins
            m.recursive |= im.recursive
            for n, (d, owner) in im.visible.items():
                # Definitions of an unnamed INSTANCE are evaluated in the instance's scope.
                m.visible[n] = (d, inst if owner is None else ("nested", inst, owner))
        for n, d in m.defs.items():
            m.visible[n] = (d, None)


# --------------------------------------------------------------------------------------------
# TLC model configuration
# --------------------------------------------------------------------------------------------

_CFG_KEYWORDS = {
    "CONSTANT": "CONSTANT", "CONSTANTS": "CONSTANT", "INIT": "INIT", "NEXT": "NEXT",
    "SPECIFICATION": "SPECIFICATION", "INVARIANT": "INVARIANT", "INVARIANTS": "INVARIANT",
    "PROPERTY": "PROPERTY", "PROPERTIES": "PROPERTY", "CONSTRAINT": "CONSTRAINT",
    "CONSTRAINTS": "CONSTRAINT", "ACTION_CONSTRAINT": "ACTION_CONSTRAINT",
    "ACTION_CONSTRAINTS": "ACTION_CONSTRAINT", "SYMMETRY": "SYMMETRY", "VIEW": "VIEW",
    "CHECK_DEADLOCK": "CHECK_DEADLOCK", "ALIAS": "ALIAS", "POSTCONDITION": "POSTCONDITION",
}


@dataclass
class Cfg:
    constants: dict = field(default_factory=dict)   # name -> concrete value
    overrides: dict = field(default_factory=dict)   # name -> definition name (`<-`)
    init: str | None = None
    next: str | None = None
    spec: str | None = None
    invariants: list = field(default_factory=list)
    properties: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    action_constraints: list = field(default_factory=list)
    symmetry: str | None = None
    view: str | None = None
    check_deadlock: bool = True


def _strip_comments(s: str) -> str:
    s = re.sub(r"\(\*.*?\*\)", " ", s, flags=re.S)
    return re.sub(r"\\\*[^\n]*", " ", s)


def parse_cfg(src: str) -> Cfg:
    cfg = Cfg()
    s = _strip_comments(src)
    i, section = 0, None
    word = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)")
    while True:
        m = word.match(s, i)
        if not m:
            if s[i:].strip():
                raise ValueError(f"unexpected cfg text {s[i:i + 40]!r}")
            return cfg
        t = m.group(1)
        i = m.end()
        if t in _CFG_KEYWORDS:
            section = _CFG_KEYWORDS[t]
            continue
        if section == "CONSTANT":
            m2 = re.compile(r"\s*(\([^)]*\))?\s*(<-|=)\s*(\[\s*[A-Za-z_][A-Za-z0-9_]*\s*\])?").match(s, i)
            if not m2 or not m2.group(2):
                raise ValueError(f"bad CONSTANT entry near {t!r}")
            i = m2.end()
            if m2.group(2) == "<-":
                m3 = word.match(s, i)
                cfg.overrides[t] = m3.group(1)
                i = m3.end()
            else:
                p = _ValueParser(s)  # identifiers are model values, as in TLC's cfg
                p.i = i
                cfg.constants[t] = p.value()
                i = p.i
        elif section in ("INIT", "NEXT", "SPECIFICATION", "SYMMETRY", "VIEW"):
            setattr(cfg, {"INIT": "init", "NEXT": "next", "SPECIFICATION": "spec",
                          "SYMMETRY": "symmetry", "VIEW": "view"}[section], t)
        elif section == "INVARIANT":
            cfg.invariants.append(t)
        elif section == "PROPERTY":
            cfg.properties.append(t)
        elif section == "CONSTRAINT":
            cfg.constraints.append(t)
        elif section == "ACTION_CONSTRAINT":
            cfg.action_constraints.append(t)
        elif section == "CHECK_DEADLOCK":
            cfg.check_deadlock = t == "TRUE"
        elif section in ("ALIAS", "POSTCONDITION"):
            pass
        else:
            raise ValueError(f"unexpected token {t!r} in cfg")


def model_values_in(v, out: set | None = None) -> set:
    out = set() if out is None else out
    if isinstance(v, ModelValue):
        out.add(v)
    elif isinstance(v, frozenset):
        for x in v:
            model_values_in(x, out)
    elif isinstance(v, Fn):
        for k, x in v.items():
            model_values_in(k, out)
            model_values_in(x, out)
    return out
