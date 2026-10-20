# TLA+ Examples with verified ranking functions

Each `gc/<spec>__<model>__<property>.gc` is a self-contained zkmc input for one property of one
finite TLC model from the [TLA+ Examples](https://github.com/tlaplus/Examples) repository
(translated by `benchmarks/tlaplus/tla2gc.py`). It contains:

- the model as guarded commands over integer slots (the slot layout and the value tables of
  enumeration slots are listed in the header comment), with `type` ranges and `init:`; an
  unconditional stutter command, because TLA+ behaviours may stutter;
- for temporal properties under fairness, the slot `act` recording which fairness-relevant
  action the last step took;
- the property: an `INVARIANT` p as `G p`, or a temporal `PROPERTY` (or one top-level conjunct of
  it) with the specification's fairness as assumption, as LTL over named state predicates in
  comments, and the Büchi automaton of its negation (Spot `ltl2tgba -B`) as `automaton_init:`
  and `trans`/`trans!` (fair) lines;
- the `rank(q)` cases for every automaton state. Finite cases are guarded by an inductive
  invariant; everything else is `inf`.

TLC verifies every model, and the translation reaches exactly TLC's reachable states. Every
ranking was checked by `zkterm_tool.verify_termination` (Farkas obligations) when it was emitted.
`index.csv` lists the model, property, program size, automaton size, number of ranking cases and
number of obligations per file.

To re-check files, run this from `encoder/` (Spot is not needed):

    uv run zkverify path/to/gc/<file>.gc
    uv run python benchmarks/mcc/reverify.py path/to/gc   # all files, in parallel
