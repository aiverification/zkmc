"""Run TLC (Docker image `zkmc-tlc:local`, see tlc/Dockerfile) and read back its results.

`explore` model-checks a model without its invariants and properties, and without symmetry
reduction or views (so every reachable state is distinct), dumping the state graph (states and
transitions, as DOT). Results are cached per model in `cache_dir`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tla_front import _strip_comments

IMAGE = os.environ.get("ZKMC_TLC_IMAGE", "zkmc-tlc:local")


@dataclass
class TLCResult:
    ok: bool
    distinct: int | None
    initial: int | None
    dump: Path | None     # the state graph, as DOT (see read_graph)
    log: str


def read_graph(dot: Path):
    """TLC's DOT dump -> (states in order of discovery, set of edges (i, j) between them)."""
    from tla_values import parse_state

    index, states, edges = {}, [], set()
    node = re.compile(r'^(-?\d+) \[label="((?:[^"\\]|\\.)*)"')
    edge = re.compile(r"^(-?\d+) -> (-?\d+)")

    def unescape(t):
        return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), t)

    pending = []
    with open(dot) as fh:
        for line in fh:
            m = edge.match(line)
            if m:
                pending.append((m.group(1), m.group(2)))
                continue
            m = node.match(line)
            if m and m.group(1) not in index:
                index[m.group(1)] = len(states)
                states.append(parse_state(unescape(m.group(2))))
    for a, b in pending:
        edges.add((index[a], index[b]))
    return states, edges


def _exploration_cfg(cfg_text: str) -> str:
    """Drop INVARIANT/PROPERTY/SYMMETRY/VIEW/ALIAS/POSTCONDITION sections and deadlock checks."""
    s = _strip_comments(cfg_text)
    drop = {"INVARIANT", "INVARIANTS", "PROPERTY", "PROPERTIES", "SYMMETRY", "VIEW", "ALIAS",
            "POSTCONDITION", "CHECK_DEADLOCK"}
    keywords = {"CONSTANT", "CONSTANTS", "INIT", "NEXT", "SPECIFICATION", "CONSTRAINT", "CONSTRAINTS",
                "ACTION_CONSTRAINT", "ACTION_CONSTRAINTS"} | drop
    out, keep = [], True
    for tok in re.split(r"(\s+)", s):
        if tok in keywords:
            keep = tok not in drop
        if keep:
            out.append(tok)
    return "".join(out) + "\nCHECK_DEADLOCK FALSE\n"


def explore(spec_dir: Path, module: str, cfg: Path, cache_dir: Path, timeout: float = 600,
            mem_mb: int = 2048, workers: int = 1) -> TLCResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta = cache_dir / "result.json"
    if meta.exists() and not (cache_dir / "states.dump").exists():
        r = json.loads(meta.read_text())
        return TLCResult(r["ok"], r["distinct"], r["initial"],
                         cache_dir / "states.dot" if r["ok"] else None, (cache_dir / "tlc.log").read_text())
    (cache_dir / "states.dump").unlink(missing_ok=True)  # the old (states-only) format
    (cache_dir / "explore.cfg").write_text(_exploration_cfg(cfg.read_text()))
    name = f"zkmc-tlc-{os.getpid()}-{cache_dir.name}"[:60]
    cmd = ["docker", "run", "--rm", "--name", name, "-u", f"{os.getuid()}:{os.getgid()}",
           "-v", f"{spec_dir.resolve()}:/spec:ro", "-v", f"{cache_dir.resolve()}:/out",
           "-e", f"JAVA_TOOL_OPTIONS=-DTLA-Library=/opt/tla/tlapm-library -XX:+UseParallelGC -Xmx{mem_mb}m "
                 "-Dtlc2.tool.impl.Tool.cdot=true",
           IMAGE, "java", "tlc2.TLC", "-workers", str(workers), "-metadir", "/tmp/meta",
           "-config", "/out/explore.cfg", "-dump", "dot,actionlabels", "/out/states", "-cleanup",
           f"/spec/{module}.tla"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        log = p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        log = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        log += "\nTIMEOUT"
        subprocess.run(["docker", "kill", name], capture_output=True)
    (cache_dir / "tlc.log").write_text(log)
    m = re.search(r"(\d+) distinct states found", log)
    distinct = int(m.group(1)) if m else None
    m = re.search(r"Finished computing initial states: (\d+) distinct state", log)
    initial = int(m.group(1)) if m else None
    ok = "Model checking completed. No error has been found." in log and distinct is not None
    json.dump({"ok": ok, "distinct": distinct, "initial": initial}, open(meta, "w"))
    return TLCResult(ok, distinct, initial, cache_dir / "states.dot" if ok else None, log)
