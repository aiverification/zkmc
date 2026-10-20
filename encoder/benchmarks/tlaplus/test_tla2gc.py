"""Tests for the TLA+ translation (run with `uv run --group tlaplus pytest benchmarks/tlaplus`)."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_tlaplus")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcc"))

import tla2gc  # noqa: E402
from run_tlaplus import reachable_states  # noqa: E402
from tla_front import parse_cfg  # noqa: E402
from tla_values import Fn, parse_dump  # noqa: E402

SPEC = r"""
---- MODULE Ring ----
EXTENDS Naturals
CONSTANT N, Procs
VARIABLES pc, tok, count
vars == <<pc, tok, count>>

Init == /\ pc = [p \in Procs |-> "idle"]
        /\ tok \in Procs
        /\ count = 0

Enter(p) == /\ pc[p] = "idle" /\ tok = p
            /\ pc' = [pc EXCEPT ![p] = "cs"]
            /\ UNCHANGED <<tok, count>>

Leave(p) == /\ pc[p] = "cs"
            /\ pc' = [pc EXCEPT ![p] = "idle"]
            /\ tok' = IF p = N THEN 1 ELSE p + 1
            /\ count' = (count + 1) % 3

Next == \E p \in Procs : Enter(p) \/ Leave(p)
Spec == Init /\ [][Next]_vars /\ \A p \in Procs : WF_vars(Enter(p) \/ Leave(p))

Mutex == \A p, q \in Procs : p # q => ~(pc[p] = "cs" /\ pc[q] = "cs")
TypeOK == pc \in [Procs -> {"idle", "cs"}] /\ tok \in Procs
Live == \A p \in Procs : <>(pc[p] = "cs")
NotLive == <>(count = 2) /\ [](tok = 1)
Ticks == []<><<\E p \in Procs : Leave(p)>>_vars
====
"""

CFG = """
CONSTANTS N = 2
          Procs = {1, 2}
SPECIFICATION Spec
INVARIANT Mutex TypeOK
PROPERTY Live NotLive Ticks
"""


def _states():
    """All reachable states of Ring, by explicit search (as TLC would dump them)."""
    def succ(s):
        pc, tok, count = s
        for p in (1, 2):
            if pc[p - 1] == "idle" and tok == p:
                yield (tuple("cs" if i == p - 1 else x for i, x in enumerate(pc)), tok, count)
            if pc[p - 1] == "cs":
                yield (tuple("idle" if i == p - 1 else x for i, x in enumerate(pc)),
                       1 if p == 2 else p + 1, (count + 1) % 3)
    seen = [(("idle", "idle"), t, 0) for t in (1, 2)]
    i = 0
    while i < len(seen):
        for t in succ(seen[i]):
            if t not in seen:
                seen.append(t)
        i += 1
    return [{"pc": Fn.tuple(list(pc)), "tok": tok, "count": c} for pc, tok, c in seen]


@pytest.fixture
def translation(tmp_path):
    (tmp_path / "Ring.tla").write_text(SPEC)
    return tla2gc.build(tmp_path, "Ring", parse_cfg(CFG), _states(), "Ring")


def test_reachable_states_match(translation):
    tr = translation
    assert tr.init_states == 2
    variables, reach = reachable_states(tr.render(None))
    enc = set()
    for st in _states():
        e = {}
        for v in tr.variables:
            e.update(tr.layouts[v].encode(st[v]))
        enc.add(tuple(e[s] for s in variables))
    assert reach == enc


def test_type_invariant_is_a_tautology(translation):
    with pytest.raises(tla2gc.Tautology):
        tla2gc.invariant_property(translation, "TypeOK")


def test_value_parser_reads_tlc_dumps():
    states = parse_dump('State 1:\n/\\ x = <<"a", 1..2>>\n/\\ y = [f |-> {m1}, g |-> (1 :> TRUE @@ 2 :> FALSE)]\n')
    assert states[0]["x"] == Fn.tuple(["a", frozenset({1, 2})])
    assert states[0]["y"]["g"][2] is False


def _spot_available() -> bool:
    try:
        from zkterm_tool.ltl import find_ltl2tgba
        find_ltl2tgba()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _spot_available(), reason="needs Spot's ltl2tgba")
def test_properties_agree_with_explicit_check(translation):
    from explicit_check import check
    from zkterm_tool.parser import parse_with_constants

    tr = translation
    verdicts = {}
    props = [tla2gc.invariant_property(tr, "Mutex")] + tla2gc.temporal_property(tr, "Live") \
        + tla2gc.temporal_property(tr, "NotLive") + tla2gc.temporal_property(tr, "Ticks")
    for prop in props:
        res = parse_with_constants(tr.render(prop), resolve_ltl=True)
        verdicts[prop.name] = check(res).holds
    assert verdicts == {"Mutex": True, "Live[p=1]": True, "Live[p=2]": True,
                        "NotLive[0]": True, "NotLive[1]": False, "Ticks": True}
