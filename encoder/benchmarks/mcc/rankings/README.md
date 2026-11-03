# MCC 2025 LTL formulas with verified ranking functions

Each `gc/<instance>-<exam>-<NN>.gc` is a self-contained zkmc input. It contains:

- the Petri net as guarded commands (one command per transition, places as integer variables) and
  its initial marking as `init:`;
- the MCC formula, converted from `LTLCardinality`/`LTLFireability`, as `ap` definitions plus
  `spec:` in comments;
- the Büchi automaton of `!(spec)` as `automaton_init:` and `trans`/`trans!` (fair) lines, as
  derived by Spot (`ltl2tgba -B`; version in the file);
- the `rank(q)` cases for every automaton state. Finite cases are guarded by the inductive
  invariant; everything else is `inf`.

Nets that can deadlock get extra stutter commands `[] <dead marking cube> -> p0 = p0`, because MCC
treats deadlocks as infinite stuttering.

All formulas are TRUE according to the MCC 2025 oracle. Every ranking was checked by
`zkterm_tool.verify_termination` (Farkas obligations) when it was emitted. `index.csv` lists the net
size, automaton size, number of ranking cases and number of obligations per file.

To re-check files, run this from `encoder/` (Spot is not needed):

    uv run zkverify path/to/gc/<file>.gc
    uv run python benchmarks/mcc/reverify.py path/to/gc   # all files, in parallel
