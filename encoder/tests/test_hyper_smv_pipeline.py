"""End-to-end tests for `.smv + .hq` HyperLTL-to-ZKMC pipeline."""

import json
import shutil

import pytest

from zkterm_tool.hyper_smv_pipeline import (
    build_hyper_smv_parse_result,
    hyper_smv_explicit_json,
    main as hyper_smv_main,
)
from zkterm_tool.ltl import find_ltl2tgba


def _spot_available() -> bool:
    if shutil.which("ltl2tgba"):
        return True
    try:
        find_ltl2tgba()
        return True
    except Exception:
        return False


requires_spot = pytest.mark.skipif(not _spot_available(), reason="Spot's ltl2tgba not installed")


LEAKY_SMV = """
MODULE main
VAR
  pc : 0..1;
  secret : 0..1;
  obs : 0..1;
ASSIGN
  init(pc) := 0;
  init(secret) := {0, 1};
  init(obs) := 0;
  next(pc) :=
    case
      pc = 0 : 1;
      TRUE : pc;
    esac;
  next(secret) := secret;
  next(obs) :=
    case
      pc = 0 : secret;
      TRUE : obs;
    esac;
"""


FIXED_SMV = """
MODULE main
VAR
  pc : 0..1;
  secret : 0..1;
  obs : 0..1;
ASSIGN
  init(pc) := 0;
  init(secret) := {0, 1};
  init(obs) := 0;
  next(pc) :=
    case
      pc = 0 : 1;
      TRUE : pc;
    esac;
  next(secret) := secret;
  next(obs) := 0;
"""


HQ_OBS_NONINTERFERENCE = 'forall A. forall B. G({"obs"_A = "obs"_B})'


COUNTER_SMV = """
MODULE main
VAR
  pc : 0..2;
ASSIGN
  init(pc) := 0;
  next(pc) :=
    case
      pc < 2 : pc + 1;
      TRUE : pc;
    esac;
"""


HQ_TWO_TRACE_PROGRESS = 'forall A. forall B. F(({"pc"_A = 2}) & ({"pc"_B = 2}))'


RANK_HARNESS = """
rank(q0):
  [] obs__A < obs__B -> inf
  [] obs__A > obs__B -> inf
  [] obs__A = obs__B -> inf

rank(q1):
  [] obs__A < obs__B -> inf
  [] obs__A > obs__B -> inf
  [] obs__A = obs__B -> 1
"""


def test_build_hyper_smv_parse_result_reduces_to_self_composed_ltl():
    result = build_hyper_smv_parse_result(
        LEAKY_SMV,
        HQ_OBS_NONINTERFERENCE,
        lowering_strategy="partition",
    )

    assert "pc__A" in result.types
    assert "pc__B" in result.types
    assert result.ltl_formula == "G(hq_ap_0)"
    assert result.aps["hq_ap_0"][0].left.name == "obs__A"
    assert result.aps["hq_ap_0"][0].right.name == "obs__B"
    assert result.commands


def test_hyper_smv_cli_emit_gc(tmp_path, capsys):
    smv_path = tmp_path / "model.smv"
    hq_path = tmp_path / "property.hq"
    smv_path.write_text(LEAKY_SMV)
    hq_path.write_text(HQ_OBS_NONINTERFERENCE)

    exit_code = hyper_smv_main([
        str(smv_path),
        str(hq_path),
        "--emit-gc",
        "--smv-lowering",
        "partition",
        "--control-var",
        "pc",
    ])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "self-composed guarded commands" in output
    assert "type pc__A: 0..1" in output
    assert 'spec: "G(hq_ap_0)"' in output


@requires_spot
def test_hyper_smv_explicit_json_detects_leaky_model():
    with pytest.raises(Exception, match="No piecewise linear ranking|ranking synthesis failed"):
        hyper_smv_explicit_json(
            LEAKY_SMV,
            HQ_OBS_NONINTERFERENCE,
            lowering_strategy="partition",
            control_variables=("pc",),
            field_size=101,
        )


@requires_spot
def test_hyper_smv_explicit_json_synthesizes_rankings_without_harness():
    output, warnings, _ = hyper_smv_explicit_json(
        COUNTER_SMV,
        HQ_TWO_TRACE_PROGRESS,
        lowering_strategy="partition",
        control_variables=("pc",),
        field_size=101,
    )

    assert output["verification"]["all_disjoint"] is True
    assert warnings == []


@requires_spot
def test_hyper_smv_cli_writes_json(tmp_path):
    smv_path = tmp_path / "model.smv"
    hq_path = tmp_path / "property.hq"
    output_path = tmp_path / "proof-input.json"
    smv_path.write_text(COUNTER_SMV)
    hq_path.write_text(HQ_TWO_TRACE_PROGRESS)

    exit_code = hyper_smv_main([
        str(smv_path),
        str(hq_path),
        "--output",
        str(output_path),
        "--pretty",
    ])

    assert exit_code == 0
    data = json.loads(output_path.read_text())
    assert data["verification"]["all_disjoint"] is True
    assert set(data["metadata"]["variables"]) == {
        "pc__A",
        "pc__B",
    }


@requires_spot
def test_hyper_smv_manual_rank_harness_still_supported():
    output, warnings, _ = hyper_smv_explicit_json(
        FIXED_SMV,
        HQ_OBS_NONINTERFERENCE,
        proof_harness_text=RANK_HARNESS,
        lowering_strategy="partition",
        control_variables=("pc",),
        field_size=101,
        synthesize=False,
    )

    assert output["verification"]["all_disjoint"] is True
    assert warnings == []
