"""Coverage summary of a TLA+ benchmark run (models, instances, proofs).

Usage: uv run python benchmarks/tlaplus/summarize.py [--work benchmarks/corpus/tlaplus]
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=Path("benchmarks/corpus/tlaplus"))
    args = ap.parse_args()

    with open(args.work / "models.csv") as fh:
        models = list(csv.DictReader(fh))
    with open(args.work / "instances.csv") as fh:
        instances = list(csv.DictReader(fh))
    proved = {}
    if (args.work / "prove.jsonl").exists():
        for line in (args.work / "prove.jsonl").read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                proved[r["instance"]] = r

    print(f"models: {len(models)} ({len({m['spec'] for m in models})} specifications)")
    for status, n in Counter(m["status"] for m in models).most_common():
        print(f"  {status:14} {n}")
    translated = [m for m in models if m["status"] == "translated"]
    print(f"translated: {len(translated)} models from {len({m['spec'] for m in translated})} specifications, "
          f"{sum(int(m['tlc_states']) for m in translated)} reachable states in total")

    for kind, title in (("inv", "invariants"), ("prop", "temporal properties")):
        rows = [i for i in instances if i["kind"] == kind]
        props = {(i["model"], i["property"]) for i in rows}
        print(f"\n{title}: {len(props)} in translated models")
        by_prop = Counter()
        for p in props:
            st = {i["status"] for i in rows if (i["model"], i["property"]) == p}
            by_prop["ok" if st == {"ok"} else "partly ok" if "ok" in st else st.pop()] += 1
        for status, n in by_prop.most_common():
            print(f"  {status:14} {n}")
        ok = [i for i in rows if i["status"] == "ok"]
        if kind == "prop":
            print(f"  {len(ok)} instances (top-level conjuncts), {sum(1 for i in ok if i['fairness'] not in ('', '0'))} "
                  f"under fairness")
        res = Counter(proved[i["instance"]]["status"] for i in ok if i["instance"] in proved)
        if res:
            print("  proved: " + ", ".join(f"{k}={v}" for k, v in res.most_common())
                  + f" (of {sum(res.values())} run)")
        failed = [i for i in rows if i["status"] not in ("ok", "tautology")]
        for reason, n in Counter(i.get("detail", "")[:60] for i in failed).most_common(8):
            print(f"    {n:3}  {reason}")
    unsupported = Counter(m["detail"][:60] for m in models if m["status"] in ("unsupported", "mismatch"))
    if unsupported:
        print("\nmodels not translated:")
        for reason, n in unsupported.most_common():
            print(f"  {n:3}  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
