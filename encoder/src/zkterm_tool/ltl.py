"""Derive a Büchi automaton from an LTL property using Spot's ``ltl2tgba``.

The user writes an LTL property ``spec:`` over named atomic propositions (``ap NAME := ...``),
each proposition being a conjunction of linear comparisons over program variables. To verify
that a program satisfies the property, we build a Büchi automaton for the **negation** of the
property (the "bad" behaviours), take its synchronous product with the program, and prove the
product has no accepting run via a ranking function. This module produces that automaton in the
same ``AutomatonTransition`` form the rest of the toolkit consumes, so nothing downstream changes.

Spot is not a Python package; it is an external tool. We shell out to the ``ltl2tgba`` binary
(``brew install spot`` / ``apt install spot`` / ``conda install -c conda-forge spot``) and parse
its HOA (Hanoi Omega-Automata) output.

Pipeline: ``ltl2tgba -B -f "!(spec)"`` → single-acceptance-set, state-based Büchi automaton (HOA)
→ parse states/edges → convert each edge's boolean label over atomic propositions to DNF →
substitute each proposition by its comparison(s), integer-negating negated propositions →
one ``trans``/``trans!`` per resulting conjunction. An edge is **fair** (in the acceptance set F,
enforcing strict ranking decrease) iff its source state is accepting — this reproduces the
hand-written automata in ``examples/`` exactly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from .ast_types import Comparison, CompOp
from .automaton_types import AutomatonTransition


# --------------------------------------------------------------------------------------
# Locating and invoking ltl2tgba (Spot)
# --------------------------------------------------------------------------------------

_INSTALL_HINT = (
    "Could not find `ltl2tgba` (part of Spot), which is required to translate LTL "
    "properties (`spec:`) into automata.\n"
    "Install Spot:\n"
    "  macOS:          brew install spot\n"
    "  Debian/Ubuntu:  apt install spot\n"
    "  conda:          conda install -c conda-forge spot\n"
    "or set the ZKTERM_LTL2TGBA environment variable to the ltl2tgba binary path.\n"
    "See https://spot.lre.epita.fr/"
)


def find_ltl2tgba(ltl2tgba_path: str | None = None) -> str:
    """Return a path to the ``ltl2tgba`` executable, or raise with an install hint."""
    if ltl2tgba_path:
        if os.path.isfile(ltl2tgba_path) and os.access(ltl2tgba_path, os.X_OK):
            return ltl2tgba_path
        raise RuntimeError(f"ltl2tgba path '{ltl2tgba_path}' is not an executable file.\n{_INSTALL_HINT}")

    env = os.environ.get("ZKTERM_LTL2TGBA")
    if env:
        if os.path.isfile(env) and os.access(env, os.X_OK):
            return env
        raise RuntimeError(f"ZKTERM_LTL2TGBA='{env}' is not an executable file.\n{_INSTALL_HINT}")

    found = shutil.which("ltl2tgba")
    if found:
        return found

    for cand in ("/opt/homebrew/bin/ltl2tgba", "/usr/local/bin/ltl2tgba", "/usr/bin/ltl2tgba"):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand

    raise RuntimeError(_INSTALL_HINT)


def run_ltl2tgba(formula: str, ltl2tgba_path: str | None = None) -> str:
    """Translate ``!(formula)`` to a state-based single-set Büchi automaton, returned as HOA text."""
    exe = find_ltl2tgba(ltl2tgba_path)
    # -B: Büchi automaton (single acceptance set, state-based). We negate the property so that
    # accepting runs of the automaton correspond to program runs that violate the property.
    cmd = [exe, "-B", "-f", f"!({formula})"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:  # pragma: no cover - defensive
        raise RuntimeError(f"Failed to run ltl2tgba: {e}\n{_INSTALL_HINT}") from e
    if proc.returncode != 0:
        raise ValueError(
            f"ltl2tgba rejected the LTL formula {formula!r}:\n{proc.stderr.strip()}"
        )
    return proc.stdout


# --------------------------------------------------------------------------------------
# Boolean label expressions over atomic-proposition indices -> DNF
# --------------------------------------------------------------------------------------

# A "cube" is one conjunction: {ap_index: polarity}. A DNF is a list of cubes (their disjunction).
Cube = dict
DNF = list


def _dnf_and(a: DNF, b: DNF) -> DNF:
    out: DNF = []
    for ca in a:
        for cb in b:
            merged = dict(ca)
            ok = True
            for k, v in cb.items():
                if k in merged and merged[k] != v:
                    ok = False
                    break
                merged[k] = v
            if ok:
                out.append(merged)
    return out


def _dnf_or(a: DNF, b: DNF) -> DNF:
    return a + b


def _dnf_not(a: DNF) -> DNF:
    # NOT(c1 | c2 | ...) = NOT(c1) & NOT(c2) & ...  where NOT(cube) = OR of negated literals.
    result: DNF = [{}]  # true
    for cube in a:
        if not cube:
            return []  # NOT(true) = false
        negated = [{k: (not v)} for k, v in cube.items()]
        result = _dnf_and(result, negated)
    return result


_LABEL_TOKEN = re.compile(r"\s*(\d+|[!&|()tf])")


def parse_label_to_dnf(label: str) -> DNF:
    """Parse a HOA edge label (boolean expression over AP indices) into DNF.

    Grammar (precedence ! > & > |): expr := term ('|' term)* ; term := factor ('&' factor)* ;
    factor := '!' factor | '(' expr ')' | 't' | 'f' | INT.
    """
    tokens: list[str] = []
    pos = 0
    label = label.strip()
    while pos < len(label):
        m = _LABEL_TOKEN.match(label, pos)
        if not m:
            raise ValueError(f"Unexpected character in HOA label {label!r} at position {pos}")
        tokens.append(m.group(1))
        pos = m.end()

    idx = 0

    def peek() -> str | None:
        return tokens[idx] if idx < len(tokens) else None

    def advance() -> str:
        nonlocal idx
        tok = tokens[idx]
        idx += 1
        return tok

    def parse_expr() -> DNF:  # OR
        result = parse_term()
        while peek() == "|":
            advance()
            result = _dnf_or(result, parse_term())
        return result

    def parse_term() -> DNF:  # AND
        result = parse_factor()
        while peek() == "&":
            advance()
            result = _dnf_and(result, parse_factor())
        return result

    def parse_factor() -> DNF:
        tok = peek()
        if tok is None:
            raise ValueError(f"Unexpected end of HOA label {label!r}")
        if tok == "!":
            advance()
            return _dnf_not(parse_factor())
        if tok == "(":
            advance()
            inner = parse_expr()
            if peek() != ")":
                raise ValueError(f"Unbalanced parentheses in HOA label {label!r}")
            advance()
            return inner
        if tok == "t":
            advance()
            return [{}]  # true
        if tok == "f":
            advance()
            return []  # false
        if tok.isdigit():
            advance()
            return [{int(tok): True}]
        raise ValueError(f"Unexpected token {tok!r} in HOA label {label!r}")

    result = parse_expr()
    if idx != len(tokens):
        raise ValueError(f"Trailing tokens in HOA label {label!r}")
    return result


# --------------------------------------------------------------------------------------
# HOA parsing
# --------------------------------------------------------------------------------------

@dataclass
class HOAEdge:
    src: int
    dst: int
    dnf: DNF                 # disjunctive normal form of the label
    edge_accepting: bool     # True if the edge carries acceptance set 0 (transition-based)


@dataclass
class HOAAutomaton:
    start_states: list[int] = field(default_factory=list)
    ap_names: list[str] = field(default_factory=list)     # index -> proposition name
    accepting_states: set[int] = field(default_factory=set)
    all_accepting: bool = False                            # Acceptance: 0 t (every run accepts)
    edges: list[HOAEdge] = field(default_factory=list)


_STATE_LINE = re.compile(r"^State:\s*(?:\[[^\]]*\]\s*)?(\d+)(.*)$")
_EDGE_LINE = re.compile(r"^(?:\[([^\]]*)\]\s*)?(\d+)(.*)$")
_ACC_SIG = re.compile(r"\{([^}]*)\}")


def parse_hoa(text: str) -> HOAAutomaton:
    """Parse the subset of HOA v1 emitted by ``ltl2tgba -B`` (state-based single-set Büchi)."""
    aut = HOAAutomaton()
    lines = text.splitlines()

    # --- Header ---
    i = 0
    n_acc_sets: int | None = None
    acc_expr = ""
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if line == "--BODY--":
            break
        if line.startswith("Start:"):
            for num in re.findall(r"\d+", line[len("Start:"):]):
                aut.start_states.append(int(num))
        elif line.startswith("AP:"):
            # AP: N "a" "b" ...
            names = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
            aut.ap_names = [n.replace('\\"', '"') for n in names]
        elif line.startswith("Acceptance:"):
            rest = line[len("Acceptance:"):].strip()
            m = re.match(r"(\d+)\s*(.*)", rest)
            if m:
                n_acc_sets = int(m.group(1))
                acc_expr = m.group(2).strip()

    if n_acc_sets is not None and n_acc_sets == 0:
        # "Acceptance: 0 t" -> every infinite run accepts; "0 f" -> none.
        aut.all_accepting = acc_expr == "t"
    elif n_acc_sets is not None and n_acc_sets > 1:
        raise ValueError(
            f"Expected a single-acceptance-set Büchi automaton, got Acceptance: {n_acc_sets} {acc_expr}. "
            "This should not happen with `ltl2tgba -B`; please report the offending formula."
        )

    # --- Body ---
    current_state: int | None = None
    while i < len(lines):
        raw = lines[i]
        i += 1
        line = raw.strip()
        if not line or line == "--END--":
            if line == "--END--":
                break
            continue

        sm = _STATE_LINE.match(line)
        if sm:
            current_state = int(sm.group(1))
            rest = sm.group(2)
            if any(tok.strip() == "0" for sig in _ACC_SIG.findall(rest) for tok in sig.split()):
                aut.accepting_states.add(current_state)
            continue

        em = _EDGE_LINE.match(line)
        if em and current_state is not None:
            label = em.group(1)
            dst = int(em.group(2))
            rest = em.group(3)
            edge_acc = any(
                tok.strip() == "0" for sig in _ACC_SIG.findall(rest) for tok in sig.split()
            )
            if label is None:
                # Implicit-labels form not emitted by `ltl2tgba -B`; be explicit about it.
                raise ValueError(
                    "HOA automaton uses implicit labels; expected explicit `[...]` edge labels "
                    "from `ltl2tgba -B`."
                )
            dnf = parse_label_to_dnf(label)
            aut.edges.append(HOAEdge(src=current_state, dst=dst, dnf=dnf, edge_accepting=edge_acc))
            continue

        raise ValueError(f"Could not parse HOA body line: {raw!r}")

    if not aut.start_states:
        raise ValueError("HOA automaton has no initial state (missing `Start:`).")
    return aut


# --------------------------------------------------------------------------------------
# Lowering HOA -> AutomatonTransition list
# --------------------------------------------------------------------------------------

def negate_comparison(c: Comparison) -> list[Comparison]:
    """Integer negation of a single comparison. ``= c`` splits into ``< c`` OR ``> c`` (two disjuncts)."""
    opposite = {
        CompOp.LT: CompOp.GE,
        CompOp.LE: CompOp.GT,
        CompOp.GT: CompOp.LE,
        CompOp.GE: CompOp.LT,
    }
    if c.op in opposite:
        return [Comparison(left=c.left, right=c.right, op=opposite[c.op])]
    if c.op == CompOp.EQ:
        return [
            Comparison(left=c.left, right=c.right, op=CompOp.LT),
            Comparison(left=c.left, right=c.right, op=CompOp.GT),
        ]
    raise ValueError(f"Cannot negate comparison operator {c.op}")


def _state_name(n: int) -> str:
    return f"q{n}"


def lower_to_transitions(
    aut: HOAAutomaton,
    aps: dict[str, list[Comparison]],
) -> tuple[list[AutomatonTransition], list[str]]:
    """Lower a parsed Büchi automaton into (transitions, initial_states) over program variables."""
    # Map AP index -> its predicate (conjunction of comparisons).
    ap_preds: dict[int, list[Comparison]] = {}
    for idx, name in enumerate(aut.ap_names):
        if name not in aps:
            raise ValueError(
                f"LTL property references atomic proposition '{name}', but no "
                f"`ap {name} := ...` binding was declared."
            )
        ap_preds[idx] = aps[name]

    transitions: list[AutomatonTransition] = []
    for edge in aut.edges:
        is_fair = aut.all_accepting or (edge.src in aut.accepting_states) or edge.edge_accepting
        for cube in edge.dnf:
            # Expand a cube into one-or-more conjunctive guards. Positive APs contribute their
            # comparisons (AND); negative APs contribute NOT(conjunction) = OR of negated
            # comparisons (branch), combined across APs by Cartesian product (AND of ORs).
            guard_options: list[list[Comparison]] = [[]]
            for idx, polarity in cube.items():
                preds = ap_preds[idx]
                if polarity:
                    guard_options = [g + list(preds) for g in guard_options]
                else:
                    disjuncts: list[Comparison] = []
                    for comp in preds:
                        disjuncts.extend(negate_comparison(comp))
                    guard_options = [g + [d] for g in guard_options for d in disjuncts]
            for guard in guard_options:
                transitions.append(
                    AutomatonTransition(
                        from_state=_state_name(edge.src),
                        to_state=_state_name(edge.dst),
                        guards=guard,
                        is_fair=is_fair,
                    )
                )

    init_states = [_state_name(s) for s in aut.start_states]
    return transitions, init_states


def derive_automaton(
    ltl_formula: str,
    aps: dict[str, list[Comparison]],
    ltl2tgba_path: str | None = None,
) -> tuple[list[AutomatonTransition], list[str]]:
    """End-to-end: LTL property + AP bindings -> (Büchi transitions, initial states)."""
    hoa_text = run_ltl2tgba(ltl_formula, ltl2tgba_path)
    aut = parse_hoa(hoa_text)
    return lower_to_transitions(aut, aps)


def resolve_automaton(result, ltl2tgba_path: str | None = None):
    """If a ParseResult carries an LTL `spec:`, derive its automaton and populate the result.

    Idempotent and a no-op when no `spec:` is present. Raises if both a `spec:` and explicit
    `trans(...)` transitions are given.
    """
    if result.ltl_formula is None:
        return result
    if result.automaton_transitions:
        raise ValueError(
            "Cannot specify both an LTL 'spec:' and explicit 'trans(...)' automaton transitions. "
            "Use one or the other."
        )
    transitions, init_states = derive_automaton(result.ltl_formula, result.aps, ltl2tgba_path)
    result.automaton_transitions = transitions
    result.automaton_initial_states = init_states
    return result
