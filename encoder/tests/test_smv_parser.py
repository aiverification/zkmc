"""Tests for the parser-only SMV import step."""

from zkterm_tool import (
    SmvBinary,
    SmvBool,
    SmvBooleanType,
    SmvCase,
    SmvName,
    SmvRangeType,
    SmvSet,
    parse_smv,
    parse_smv_file,
)
from zkterm_tool.smv_cli import format_smv_model


SIMPLE_SMV = """
-- Tiny deterministic SMV model.
MODULE main

VAR
  pc : 0..2;
  flag : boolean;
  mode : {idle, busy};

DEFINE
  start := 0;
  done := 2;
  ready := (pc = start) & flag;

ASSIGN
  init(pc) := start;
  init(flag) := FALSE;
  init(mode) := idle;

  next(pc) :=
    case
      pc = start : 1;
      pc = 1 : done;
      TRUE : pc;
    esac;

  next(flag) := TRUE;
  next(mode) := {idle, busy};
"""


def test_parse_variables_and_defines():
    model = parse_smv(SIMPLE_SMV)

    assert model.module == "main"
    assert len(model.variables) == 3

    var_map = model.variable_map()
    assert var_map["pc"] == SmvRangeType(0, 2)
    assert isinstance(var_map["flag"], SmvBooleanType)
    assert repr(var_map["mode"]) == "{idle, busy}"

    define_map = model.define_map()
    assert define_map["start"].value == 0
    assert define_map["done"].value == 2
    assert isinstance(define_map["ready"], SmvBinary)
    assert define_map["ready"].op == "&"


def test_parse_init_and_next_assignments():
    model = parse_smv(SIMPLE_SMV)

    inits = model.init_assignments()
    nexts = model.next_assignments()

    assert [assignment.target for assignment in inits] == ["pc", "flag", "mode"]
    assert [assignment.target for assignment in nexts] == ["pc", "flag", "mode"]

    assert isinstance(inits[1].expr, SmvBool)
    assert inits[1].expr.value is False
    assert isinstance(inits[2].expr, SmvName)
    assert inits[2].expr.name == "idle"


def test_parse_case_expression_and_set_assignment():
    model = parse_smv(SIMPLE_SMV)

    pc_next = next(assignment for assignment in model.next_assignments() if assignment.target == "pc")
    assert isinstance(pc_next.expr, SmvCase)
    assert len(pc_next.expr.arms) == 3
    assert isinstance(pc_next.expr.arms[0].guard, SmvBinary)
    assert pc_next.expr.arms[0].guard.op == "="
    assert pc_next.expr.arms[2].guard == SmvBool(True)

    mode_next = next(assignment for assignment in model.next_assignments() if assignment.target == "mode")
    assert isinstance(mode_next.expr, SmvSet)
    assert [value.name for value in mode_next.expr.values] == ["idle", "busy"]


def test_parse_smv_file(tmp_path):
    path = tmp_path / "simple.smv"
    path.write_text(SIMPLE_SMV)

    model = parse_smv_file(str(path))

    assert model.module == "main"
    assert len(model.assignments) == 6


def test_cli_formatter_outputs_parsed_sections():
    model = parse_smv(SIMPLE_SMV)
    output = format_smv_model(model)

    assert "MODULE main" in output
    assert "VAR" in output
    assert "pc : 0..2;" in output
    assert "DEFINE" in output
    assert "ASSIGN" in output
    assert "next(pc) :=" in output
