#!/usr/bin/env python3
"""Run one selected benchmark and store raw, plot-ready performance data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "benchmarks" / "selected_benchmarks.json"
UPSTREAM = ROOT / ".vendor" / "zkmc-upstream"
VENV = ROOT / ".venv"
GENERATED_CONFIG = ROOT / "src" / "generated_config.rs"
RESULT_ROOT = ROOT / "artifacts" / "benchmarks"
METRIC_PATTERN = re.compile(r"^METRIC ([A-Za-z0-9_]+)=([^\s]+)$")
CONFIG_PATTERN = re.compile(r"pub const (\w+): usize = (\d+);")


def load_cases() -> list[dict[str, Any]]:
    """Load the intentionally limited benchmark set."""
    return json.loads(CONFIG_PATH.read_text())


def find_case(name: str) -> dict[str, Any]:
    """Return one selected benchmark configuration."""
    for case in load_cases():
        if case["name"] == name:
            return case
    raise ValueError(f"unknown selected benchmark: {name}")


def output_line(log, text: str) -> None:
    """Write one line to both the console and benchmark log."""
    print(text, flush=True)
    log.write(text + "\n")
    log.flush()


def parse_peak_rss(path: Path) -> int | None:
    """Read Linux /usr/bin/time maximum resident set size."""
    if not path.exists():
        return None
    for line in path.read_text(errors="replace").splitlines():
        if "Maximum resident set size (kbytes):" in line:
            return int(line.rsplit(":", 1)[1].strip())
    return None


def run_command(
    label: str,
    command: list[str],
    run_dir: Path,
    log,
    *,
    stdout_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one measured subprocess and preserve its complete output."""
    time_file = run_dir / f"time_{label}.txt"
    wrapped = ["/usr/bin/time", "-v", "-o", str(time_file), *command]
    output_line(log, f"COMMAND {label}: {' '.join(command)}")
    started = time.perf_counter()

    if stdout_path is not None:
        with stdout_path.open("wb") as output:
            process = subprocess.Popen(
                wrapped,
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.PIPE,
                env=env,
            )
            assert process.stderr is not None
            for raw in iter(process.stderr.readline, b""):
                line = raw.decode(errors="replace").rstrip("\n")
                output_line(log, line)
            return_code = process.wait()
    else:
        process = subprocess.Popen(
            wrapped,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            output_line(log, line.rstrip("\n"))
        return_code = process.wait()

    elapsed = time.perf_counter() - started
    result = {
        f"{label}_seconds": elapsed,
        f"{label}_peak_rss_kb": parse_peak_rss(time_file),
    }
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)
    return result


def command_output(command: list[str]) -> str:
    """Return short reproducibility metadata without failing the run."""
    try:
        return subprocess.check_output(command, cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def sha256(path: Path) -> str:
    """Hash one artifact file."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_rust_metrics(log_path: Path) -> dict[str, int | float]:
    """Collect stable METRIC records printed by the Rust binary."""
    metrics: dict[str, int | float] = {}
    for line in log_path.read_text(errors="replace").splitlines():
        match = METRIC_PATTERN.match(line)
        if not match:
            continue
        key, text = match.groups()
        metrics[key] = float(text) if any(char in text for char in ".eE") else int(text)
    return metrics


def parse_generated_config(path: Path) -> dict[str, int]:
    """Read the fixed circuit dimensions used for this build."""
    return {name.lower(): int(value) for name, value in CONFIG_PATTERN.findall(path.read_text())}


def machine_metadata() -> dict[str, Any]:
    """Record enough environment data to interpret performance plots."""
    cpu_model = "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(errors="replace").splitlines():
            if line.lower().startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    ram_bytes = None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        match = re.search(r"MemTotal:\s+(\d+) kB", meminfo.read_text())
        if match:
            ram_bytes = int(match.group(1)) * 1024
    return {
        "cpu_model": cpu_model,
        "logical_cores": os.cpu_count(),
        "ram_bytes": ram_bytes,
        "operating_system": platform.platform(),
        "python_version": platform.python_version(),
        "rustc_version": command_output(["rustc", "--version"]),
        "cargo_version": command_output(["cargo", "--version"]),
        "project_commit": command_output(["git", "rev-parse", "HEAD"]),
        "sonobe_commit": command_output(
            ["git", "-C", str(ROOT / ".vendor" / "sonobe-259"), "rev-parse", "HEAD"]
        ),
        "zkmc_upstream_commit": command_output(
            ["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"]
        ),
    }


def prepare_program(case: dict[str, Any], run_dir: Path, phases: dict[str, Any], log) -> Path:
    """Resolve an EXB model or generate one exact DHCP variant."""
    if case["family"] == "exb":
        return UPSTREAM / "encoder" / "examples" / case["program_file"]

    dhcp = case["dhcp"]
    program = run_dir / f"{case['name']}.gc"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "generate_dhcp.py"),
        str(program),
        "--w1",
        str(dhcp["w1"]),
        "--attempts",
        str(dhcp["attempts"]),
        "--w2",
        str(dhcp["w2"]),
    ]
    if dhcp["no_offered_state"]:
        command.append("--no-offered-state")
    phases.update(run_command("model_generation", command, run_dir, log))
    return program


def collect_result(
    case: dict[str, Any],
    repeat: int,
    run_dir: Path,
    phases: dict[str, Any],
    status: str,
    error: str | None,
    last_phase: str,
) -> dict[str, Any]:
    """Assemble one self-contained JSON result row."""
    log_path = run_dir / "run.log"
    metrics: dict[str, Any] = {
        "benchmark": case["name"],
        "family": case["family"],
        "repeat": repeat,
        "status": status,
        "error": error,
        "last_completed_phase": last_phase,
        "expected_obligation_count": case["expected_obligations"],
        "paper_state_space_log2": case.get("paper_state_space_log2"),
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **machine_metadata(),
        **phases,
    }
    if case["family"] == "exb":
        metrics["initial_delay"] = case["constants"]["initialDelay"]
        metrics["max_attempts"] = case["constants"]["maxAttempts"]
    else:
        metrics["dhcp_w1"] = case["dhcp"]["w1"]
        metrics["max_attempts"] = case["dhcp"]["attempts"]
        metrics["dhcp_w2"] = case["dhcp"]["w2"]
        metrics["no_offered_state"] = case["dhcp"]["no_offered_state"]
    if log_path.exists():
        metrics.update(parse_rust_metrics(log_path))

    official_path = run_dir / "official.json"
    batch_path = run_dir / "batch.json"
    config_path = run_dir / "generated_config.rs"
    if official_path.exists():
        metrics["official_json_bytes"] = official_path.stat().st_size
        try:
            official = json.loads(official_path.read_text())
            metrics["obligation_count"] = int(official["count"])
            obligations = official.get("obligations", [])
            metrics["init_count"] = sum(
                item["obligation_type"] == "initial_non_infinity"
                for item in obligations
            )
            metrics["fair_count"] = sum(
                item["obligation_type"] == "update" and item.get("is_fair", False)
                for item in obligations
            )
            metrics["step_count"] = (
                len(obligations) - metrics["init_count"] - metrics["fair_count"]
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exception:
            metrics["official_parse_error"] = str(exception)
    if batch_path.exists():
        metrics["batch_json_bytes"] = batch_path.stat().st_size
        try:
            batch = json.loads(batch_path.read_text())
            metrics["bound"] = batch["bound"]
        except (KeyError, TypeError, json.JSONDecodeError) as exception:
            metrics["batch_parse_error"] = str(exception)
    if config_path.exists():
        try:
            metrics.update(parse_generated_config(config_path))
        except (OSError, ValueError) as exception:
            metrics["config_parse_error"] = str(exception)

    proof_dir = run_dir / "proof"
    artifact_names = {
        "statement_bytes": run_dir / "statement.json",
        "proof_bytes": proof_dir / "decider_proof.bin",
        "verifier_parameter_bytes": proof_dir / "decider_verifier.bin",
        "public_input_bytes": proof_dir / "decider_public.bin",
        "manifest_bytes": proof_dir / "manifest.json",
    }
    total = 0
    hashes: dict[str, str] = {}
    for key, path in artifact_names.items():
        if path.exists():
            size = path.stat().st_size
            metrics[key] = size
            total += size
            hashes[path.name] = sha256(path)
    if total:
        metrics["total_artifact_bytes"] = total
        (run_dir / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")

    rss_values = [value for key, value in metrics.items() if key.endswith("_peak_rss_kb") and isinstance(value, int)]
    metrics["peak_rss_kb"] = max(rss_values, default=None)
    if all(key in metrics for key in ("nova_setup_seconds", "decider_setup_seconds")):
        metrics["setup_seconds"] = metrics["nova_setup_seconds"] + metrics["decider_setup_seconds"]
    if all(key in metrics for key in ("nova_fold_total_seconds", "decider_prove_seconds")):
        metrics["prover_seconds"] = metrics["nova_fold_total_seconds"] + metrics["decider_prove_seconds"]
    metrics["verifier_seconds"] = metrics.get("standalone_verify_seconds")
    return metrics


def main() -> None:
    """Execute one measured benchmark run."""
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmark")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    case = find_case(args.benchmark)
    run_dir = RESULT_ROOT / case["name"] / f"run_{args.repeat:02d}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    log_path = run_dir / "run.log"
    original_config = GENERATED_CONFIG.read_bytes()
    phases: dict[str, Any] = {}
    status = "error"
    error: str | None = None
    last_phase = "started"
    total_start = time.perf_counter()

    try:
        with log_path.open("a", encoding="utf-8") as log:
            program = prepare_program(case, run_dir, phases, log)
            last_phase = "model_ready"

            official_path = run_dir / "official.json"
            farkas_command = [str(VENV / "bin" / "zkfarkas"), "--pretty", str(program)]
            for name, value in case.get("constants", {}).items():
                farkas_command.extend(["--const", f"{name}={value}"])
            phases.update(
                run_command(
                    "farkas",
                    farkas_command,
                    run_dir,
                    log,
                    stdout_path=official_path,
                )
            )
            last_phase = "farkas"

            batch_path = run_dir / "batch.json"
            adapt_command = [
                str(VENV / "bin" / "python"),
                str(ROOT / "scripts" / "adapt_official.py"),
                str(official_path),
                str(batch_path),
                str(GENERATED_CONFIG),
                "--benchmark",
                case["name"],
                "--expected-count",
                str(case["expected_obligations"]),
            ]
            phases.update(run_command("adapt", adapt_command, run_dir, log))
            shutil.copy2(GENERATED_CONFIG, run_dir / "generated_config.rs")
            last_phase = "adapt"

            phases.update(
                run_command(
                    "cargo_build",
                    ["cargo", "build", "--release", "--locked"],
                    run_dir,
                    log,
                )
            )
            binary = ROOT / "target" / "release" / "zkmc"
            last_phase = "build"

            statement = run_dir / "statement.json"
            phases.update(
                run_command(
                    "statement",
                    [str(binary), "commit", str(batch_path), str(statement)],
                    run_dir,
                    log,
                )
            )
            last_phase = "statement"

            proof_dir = run_dir / "proof"
            phases.update(
                run_command(
                    "prove",
                    [
                        str(binary),
                        "decider",
                        str(batch_path),
                        str(proof_dir),
                        str(statement),
                    ],
                    run_dir,
                    log,
                    env={**os.environ, "RUST_BACKTRACE": "1"},
                )
            )
            last_phase = "proof"

            trusted_dir = RESULT_ROOT / "trusted_verifiers"
            trusted_dir.mkdir(parents=True, exist_ok=True)
            trusted_verifier = trusted_dir / f"{case['name']}.bin"
            shutil.copy2(proof_dir / "decider_verifier.bin", trusted_verifier)
            phases.update(
                run_command(
                    "standalone_verify_command",
                    [str(binary), "verify", str(proof_dir), str(statement), str(trusted_verifier)],
                    run_dir,
                    log,
                )
            )
            last_phase = "standalone_verification"
            status = "ok"
    except Exception as exception:  # Preserve partial data before returning failure.
        error = f"{type(exception).__name__}: {exception}"
        print(error, file=sys.stderr)
    finally:
        GENERATED_CONFIG.write_bytes(original_config)
        phases["total_pipeline_seconds"] = time.perf_counter() - total_start
        result = collect_result(case, args.repeat, run_dir, phases, status, error, last_phase)
        (run_dir / "metrics.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "aggregate_metrics.py")],
            cwd=ROOT,
            check=False,
        )

    if status != "ok":
        raise SystemExit(1)
    print(f"BENCHMARK COMPLETE: {case['name']} run {args.repeat}")
    print(f"Metrics: {run_dir / 'metrics.json'}")


if __name__ == "__main__":
    main()
