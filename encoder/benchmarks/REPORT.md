# KoAT Complexity_ITS — termination-synthesis coverage

Coverage of zkmc's **symbolic** termination synthesis (single-scalar piecewise-linear rankings, no
bounds) over KoAT's own **Complexity_ITS** benchmark suite from TermComp's TPDB — **838 `.koat`
files**. Each file is imported → a ranking is synthesized → the ranking is verified; the outcome is
categorized. A complexity benchmark that has a bound also terminates, so these are valid termination
inputs. Reproduce with `benchmarks/fetch_corpus.sh` + `benchmarks/run_its.py`.

## Results (838 files, 8 parallel jobs)

Two search-depth settings for the partition search (`--max-mode-vars`):

| Outcome                | `mmv=1` (timeout 25s, 20 min) | `mmv=2` (timeout 20s, 36 min) |
|------------------------|------------------------------:|------------------------------:|
| **pass**               | **80 (9.5%)**                 | **91 (10.9%)**                |
| no-ranking             | 557 (66.5%)                   | 325 (38.8%)                   |
| timeout                | 136 (16.2%)                   | 357 (42.6%)                   |
| unsupported-nonlinear  | 65 (7.8%)                     | 65 (7.8%)                     |

`pass` = ranking synthesized, **validated** (disjoint/covering/non-negative) **and re-verified**.
Passing programs are small (median 2 locations, 1–24 ranking cases; synth median ≈0.1 s).

### Environment and reproducibility

Numbers above were produced on an **Apple M4 Max (16 cores), 128 GB RAM, macOS 26.5.2**, with the
TPDB corpus pinned by `fetch_corpus.sh`. The `pass` count is largely stable across machines, but
the **`timeout` vs `no-ranking` split is machine-dependent**: each file is classified against a
wall-clock `--timeout`, so a slower/faster machine moves borderline files between those buckets
(and can gain/lose a couple of passes at the boundary). This is most pronounced at `mmv=2`, where
~43% of files hit the timeout. Expect the table to reproduce approximately, not exactly.

## Why we can't handle the rest

- **no-ranking — the dominant gap (~66% at `mmv=1`).** The search exhausts our single-scalar model
  and finds no ranking. These need a **multiphase / lexicographic ranking (MΦRF) or an inferred
  invariant** — which the current single-non-negative-scalar obligation model cannot express over
  unbounded integers (documented as the deferred lexicographic-obligation extension). **This is the
  #1 limiter and quantifies the value of that future work.**
- **timeout — mostly a search-cost artifact.** At `mmv=2` the coarsest-first search grinds through
  *pair* partitions (43% timeout); at `mmv=1` (no pairs) most of those resolve to `no-ranking` within
  25 s, leaving 16% genuinely-too-slow (the largest programs). The pair search buys only ~11 extra
  passes at ~2× wall-clock — so **`mmv=1` is the better corpus-scale operating point**; reserve
  `mmv=2` for individual hard cases. Optimizing the feasibility-pruning cost is a secondary lever.
- **unsupported-nonlinear (7.8%).** The `twn` / `non_linear` suites (Lommen_22/23/24) use
  non-linear arithmetic (`^` powers, variable products) and disequality (`!=`) guards — outside the
  linear-arithmetic fragment. The importer rejects all of them loudly at import time (files
  containing `^` used to be split between a "parse" bucket and silent mis-parses; they are now all
  classified here).

## Takeaways

1. Single-scalar synthesis handles **~10%** of Complexity_ITS today; **~66% need multiphase /
   lexicographic** rankings — so the **MΦRF / lexicographic-obligation extension is the highest-value
   next step**.
2. Synthesis **performance** (pair-partition feasibility pruning) is a secondary limiter; a smaller
   search (`mmv=1`) or a pruning optimization reclaims most of the timeout bucket.
3. **ZK pipeline (next):** run `zkmc-symbolic` on the ~80–91 passing benchmarks via
   `run_its.py --emit-farkas DIR` (which dumps the Farkas-dual JSON), then prove/verify with the Rust
   backend and record setup/prove/verify times.

## Reproduce

```bash
bash benchmarks/fetch_corpus.sh
uv run python benchmarks/run_its.py --corpus benchmarks/corpus/Complexity_ITS \
    --jobs 8 --timeout 25 --max-mode-vars 1 --out benchmarks/stats_mmv1.csv
uv run python benchmarks/run_its.py --corpus benchmarks/corpus/Complexity_ITS \
    --jobs 8 --timeout 20 --max-mode-vars 2 --out benchmarks/stats_mmv2.csv
```
