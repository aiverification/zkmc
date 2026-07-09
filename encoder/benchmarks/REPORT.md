# KoAT Complexity_ITS — termination-synthesis coverage

Coverage of zkmc's **symbolic** termination synthesis (single-scalar piecewise-linear rankings, no
bounds) over KoAT's own **Complexity_ITS** benchmark suite from TermComp's TPDB — **838 `.koat`
files**. Each file is imported → a ranking is synthesized → the ranking is verified; the outcome is
categorized. A complexity benchmark that has a bound also terminates, so these are valid termination
inputs. Reproduce with `benchmarks/fetch_corpus.sh` + `benchmarks/run_its.py`.

## Results (838 files, 8 parallel jobs)

Two search-depth settings for the partition search (`--max-mode-vars`):

| Outcome                | `mmv=1` (timeout 25s, 11 min) | `mmv=2` (timeout 20s, 18 min) |
|------------------------|------------------------------:|------------------------------:|
| **pass**               | **81 (9.7%)**                 | **93 (11.1%)**                |
| no-ranking             | 596 (71.1%)                   | 340 (40.6%)                   |
| timeout                | 120 (14.3%)                   | 364 (43.4%)                   |
| unsupported-nonlinear  | 26 (3.1%)                     | 26 (3.1%)                     |
| unsupported-parse      | 15 (1.8%)                     | 15 (1.8%)                     |

`pass` = ranking synthesized **and re-verified**. Passing programs are small (median 2 locations,
1–16 ranking cases; synth median 0.16 s).

## Why we can't handle the rest

- **no-ranking — the dominant gap (~71% at `mmv=1`).** The search exhausts our single-scalar model
  and finds no ranking. These need a **multiphase / lexicographic ranking (MΦRF) or an inferred
  invariant** — which the current single-non-negative-scalar obligation model cannot express over
  unbounded integers (documented as the deferred lexicographic-obligation extension). **This is the
  #1 limiter and quantifies the value of that future work.**
- **timeout — mostly a search-cost artifact.** At `mmv=2` the coarsest-first search grinds through
  *pair* partitions (43% timeout); at `mmv=1` (no pairs) most of those resolve to `no-ranking` within
  25 s, leaving 14% genuinely-too-slow (the largest programs). The pair search buys only ~12 extra
  passes at ~3× wall-clock — so **`mmv=1` is the better corpus-scale operating point**; reserve
  `mmv=2` for individual hard cases. Optimizing the feasibility-pruning cost is a secondary lever.
- **unsupported-nonlinear / -parse (~5%).** The `twn` / `non_linear` suites (Lommen_22/23/24) use
  non-linear updates (`^` powers) and disequality (`!=`) guards — outside the linear-arithmetic
  fragment. (The 15 "parse" failures are the same nonlinear files; the importer just rejects `^`/`!=`
  at the grammar rather than at encoding.)

## Takeaways

1. Single-scalar synthesis handles **~10%** of Complexity_ITS today; **~71% need multiphase /
   lexicographic** rankings — so the **MΦRF / lexicographic-obligation extension is the highest-value
   next step**.
2. Synthesis **performance** (pair-partition feasibility pruning) is a secondary limiter; a smaller
   search (`mmv=1`) or a pruning optimization reclaims most of the timeout bucket.
3. **ZK pipeline (next):** run `zkmc-symbolic` on the ~80–93 passing benchmarks via
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
