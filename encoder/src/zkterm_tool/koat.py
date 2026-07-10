"""Importer for the KoAT / termCOMP integer-transition-system (`.koat`) format.

A `.koat` file describes an integer transition system: variables, a start location, and rules
`l(vars) -> Com_1(l'(updates)) :|: guard`. We import it into the existing IR:

- each **location** becomes a value of a fresh `pc` variable (`type pc: 0..L-1`);
- each **rule** becomes a guarded command `[] pc == src && guard -> <updates>; pc = dst`;
- **fresh variables** (declared in `VAR` but not a location parameter) are nondeterministic inputs,
  modelled as always-**havoc'd** variables;
- since ITS benchmarks pose a *termination* question, we attach the **all-fair termination automaton**
  `automaton_init: q0` / `trans!(q0,q0): true` — every transition must strictly decrease the ranking.

Data variables are left **untyped** (unbounded): the symbolic path proves termination over the
integers. Only `pc` is bounded (finitely many locations). `Com_n` (n>1, recursion) and non-linear
updates are rejected. Nondeterministic branching is expressed as multiple rules (guarded commands).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from lark import Lark, Transformer

from .ast_types import Comparison, CompOp, Expr, Var, Num, BinOp, Neg, Assignment, TypeDef, GuardedCommand
from .automaton_types import AutomatonTransition
from .parser import ParseResult


_GRAMMAR_PATH = Path(__file__).parent / "koat_grammar.lark"


@dataclass
class _FTerm:
    name: str
    args: List[Expr]


@dataclass
class _Rule:
    src: _FTerm
    targets: List[_FTerm]
    com: str
    guard: List[Comparison]


class _KoatTransformer(Transformer):
    # --- arithmetic ---
    def number(self, items):
        return Num(int(items[0]))

    def var(self, items):
        return Var(str(items[0]))

    def add(self, items):
        return BinOp("+", items[0], items[1])

    def sub(self, items):
        return BinOp("-", items[0], items[1])

    def mul(self, items):
        return BinOp("*", items[0], items[1])

    def neg(self, items):
        return Neg(items[0])

    # --- comparisons / guards ---
    def comparison(self, items):
        left, op_tok, right = items
        op_str = str(op_tok)
        op = {">=": CompOp.GE, "<=": CompOp.LE, "==": CompOp.EQ, "=": CompOp.EQ,
              ">": CompOp.GT, "<": CompOp.LT}[op_str]
        return Comparison(left=left, right=right, op=op)

    def guard(self, items):
        return list(items)

    def guardpart(self, items):
        return items[0]  # the guard list

    # --- terms / rules ---
    def arglist(self, items):
        return list(items)

    def fterm(self, items):
        name = str(items[0])
        args = items[1] if len(items) > 1 else []
        return _FTerm(name=name, args=args)

    def rhs_com(self, items):
        com = str(items[0])
        targets = list(items[1:])
        return (com, targets)

    def rhs_bare(self, items):
        return ("Com_1", [items[0]])

    def rule(self, items):
        src = items[0]
        com, targets = items[1]
        guard = items[2] if len(items) > 2 and items[2] is not None else []
        return _Rule(src=src, targets=targets, com=com, guard=guard)

    def rules(self, items):
        return list(items)

    def var_decl(self, items):
        return [str(t) for t in items]

    def startterm(self, items):
        return [str(t) for t in items]

    def goalbody(self, items):
        return None

    def goal(self, items):
        return None

    def start(self, items):
        var_list: List[str] = []
        start_locs: List[str] = []
        rules: List[_Rule] = []
        for it in items:
            if isinstance(it, list) and it and isinstance(it[0], _Rule):
                rules = it
            elif isinstance(it, list) and it and isinstance(it[0], str):
                # var_decl or startterm both produce list[str]; distinguish by which came first.
                if not start_locs:
                    start_locs = it
                else:
                    var_list = it
            elif isinstance(it, list):
                # empty list (e.g. no VARs) — assign to whichever slot is still empty
                if not start_locs:
                    start_locs = it
                else:
                    var_list = it
        return {"vars": var_list, "start": start_locs, "rules": rules}


def _create_parser() -> Lark:
    return Lark(_GRAMMAR_PATH.read_text(), parser="lalr")


def _pc_name(variables: List[str]) -> str:
    name = "pc"
    while name in variables:
        name = "_" + name
    return name


def import_koat(text: str) -> ParseResult:
    """Parse a `.koat` integer transition system into a ParseResult (termination framing)."""
    tree = _create_parser().parse(text)
    parsed = _KoatTransformer().transform(tree)

    var_list: List[str] = parsed["vars"]
    start_locs: List[str] = parsed["start"]
    rules: List[_Rule] = parsed["rules"]

    if not rules:
        raise ValueError("KoAT file has no rules.")
    if not start_locs:
        raise ValueError("KoAT file has no STARTTERM/FUNCTIONSYMBOLS start location.")
    start_loc = start_locs[0]

    # Formals = the parameter names of the first rule's left-hand side (must be plain variables).
    def formals_of(ft: _FTerm) -> List[str]:
        names = []
        for a in ft.args:
            if not isinstance(a, Var):
                raise ValueError(f"Location head {ft.name}(...) must list plain variables, got {a!r}")
            names.append(a.name)
        return names

    formals = formals_of(rules[0].src)
    for r in rules:
        if formals_of(r.src) != formals:
            raise ValueError(
                f"All rule heads must use the same parameter names in order; expected {formals}, "
                f"got {formals_of(r.src)} in a rule from {r.src.name}."
            )

    # Program variables = declared VARs (fall back to the formals if VAR is absent).
    program_vars = var_list or list(formals)
    fresh = [v for v in program_vars if v not in formals]  # nondeterministic inputs

    # Locations -> indices, in order of first appearance (start location first).
    locations: List[str] = [start_loc]
    for r in rules:
        for ft in [r.src] + r.targets:
            if ft.name not in locations:
                locations.append(ft.name)
    loc_idx = {name: i for i, name in enumerate(locations)}

    pc = _pc_name(program_vars)
    types = {pc: TypeDef(variable=pc, min_value=0, max_value=len(locations) - 1)}

    init_condition = [Comparison(left=Var(pc), right=Num(loc_idx[start_loc]), op=CompOp.EQ)]

    commands: List[GuardedCommand] = []
    havoc_set = frozenset(fresh)
    for r in rules:
        if len(r.targets) != 1:
            raise ValueError(
                f"Rule from {r.src.name} uses {r.com} with {len(r.targets)} successors; recursion / "
                "Com_n (n>1) is not supported."
            )
        tgt = r.targets[0]
        if len(tgt.args) != len(formals):
            raise ValueError(
                f"Successor {tgt.name} has arity {len(tgt.args)}, expected {len(formals)}."
            )
        guards = [Comparison(left=Var(pc), right=Num(loc_idx[r.src.name]), op=CompOp.EQ)] + list(r.guard)
        assignments: List[Assignment] = []
        for formal, expr in zip(formals, tgt.args):
            if not (isinstance(expr, Var) and expr.name == formal):  # skip identity updates
                assignments.append(Assignment(var=formal, expr=expr))
        assignments.append(Assignment(var=pc, expr=Num(loc_idx[tgt.name])))
        commands.append(GuardedCommand(guards=guards, assignments=assignments, havoc=havoc_set))

    # All-fair termination automaton: every transition must strictly decrease the ranking.
    automaton = [AutomatonTransition(from_state="q0", to_state="q0", guards=[], is_fair=True)]

    return ParseResult(
        constants={},
        types=types,
        init_condition=init_condition,
        commands=commands,
        ranking_functions={},
        automaton_transitions=automaton,
        automaton_initial_states=["q0"],
        aps={},
        ltl_formula=None,
        observable_symbols=set(program_vars) | {pc},
    )


def import_koat_file(path: str) -> ParseResult:
    return import_koat(Path(path).read_text())
