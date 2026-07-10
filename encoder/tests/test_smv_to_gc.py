"""Tests for lowering parsed SMV models into guarded-command IR."""

import pytest

from zkterm_tool import import_smv, parse_smv, smv_to_gc
from zkterm_tool.ast_types import BinOp, CompOp, Num, Var
from zkterm_tool.smv_cli import format_gc_result_from_smv, main as smv_cli_main


def test_lower_types_and_init_conditions():
    result = import_smv("""
MODULE main
VAR
  pc : 0..1;
  flag : boolean;
  mode : {idle, busy};
ASSIGN
  init(pc) := 0;
  init(flag) := FALSE;
  init(mode) := idle;
  next(pc) := pc;
  next(flag) := TRUE;
  next(mode) := busy;
""")

    assert result.types["pc"].min_value == 0
    assert result.types["pc"].max_value == 1
    assert result.types["flag"].min_value == 0
    assert result.types["flag"].max_value == 1
    assert result.types["mode"].min_value == 0
    assert result.types["mode"].max_value == 1

    assert result.init_condition == [
        # pc = 0, flag = FALSE, mode = idle
        result.init_condition[0],
        result.init_condition[1],
        result.init_condition[2],
    ]
    assert [(c.left, c.op, c.right) for c in result.init_condition] == [
        (Var("pc"), CompOp.EQ, Num(0)),
        (Var("flag"), CompOp.EQ, Num(0)),
        (Var("mode"), CompOp.EQ, Num(0)),
    ]


def test_lower_case_with_priority():
    result = import_smv("""
MODULE main
VAR
  x : 0..2;
ASSIGN
  init(x) := 0;
  next(x) :=
    case
      TRUE : 1;
      TRUE : 2;
    esac;
""")

    assert len(result.commands) == 1
    assert result.commands[0].assignments[0].var == "x"
    assert result.commands[0].assignments[0].expr == Num(1)


def test_lower_case_guards_and_defines():
    result = import_smv("""
MODULE main
VAR
  pc : 0..2;
DEFINE
  start := 0;
ASSIGN
  init(pc) := start;
  next(pc) :=
    case
      pc = start : 1;
      TRUE : pc;
    esac;
""")

    assert len(result.commands) == 2

    first = result.commands[0]
    assert first.guards == [first.guards[0]]
    assert (first.guards[0].left, first.guards[0].op, first.guards[0].right) == (
        Var("pc"),
        CompOp.EQ,
        Num(0),
    )
    assert first.assignments[0].expr == Num(1)

    fallback = result.commands[1]
    assert fallback.assignments[0].expr == Var("pc")
    assert any(g.op in {CompOp.LT, CompOp.GT} for g in fallback.guards)


def test_lower_set_next_to_nondeterministic_commands():
    result = import_smv("""
MODULE main
VAR
  mode : {idle, busy};
ASSIGN
  init(mode) := idle;
  next(mode) := {idle, busy};
""")

    assert len(result.commands) == 2
    assert [cmd.assignments[0].expr for cmd in result.commands] == [Num(0), Num(1)]


def test_missing_next_assignment_is_havoced():
    result = import_smv("""
MODULE main
VAR
  x : 0..1;
  y : 0..1;
ASSIGN
  init(x) := 0;
  next(x) := x + 1;
""")

    assert len(result.commands) == 1
    assert result.commands[0].assignments[0].expr == BinOp("+", Var("x"), Num(1))
    assert result.commands[0].havoc == frozenset({"y"})


def test_non_full_nondeterministic_init_is_rejected():
    with pytest.raises(ValueError, match="init\\(x\\).*cannot"):
        import_smv("""
MODULE main
VAR
  x : 0..2;
ASSIGN
  init(x) := {0, 1};
  next(x) := x;
""")


def test_full_domain_nondeterministic_init_is_unconstrained():
    result = import_smv("""
MODULE main
VAR
  x : 0..1;
ASSIGN
  init(x) := {0, 1};
  next(x) := x;
""")

    assert result.init_condition is None


def test_smv_to_gc_and_cli_formatter():
    model = parse_smv("""
MODULE main
VAR
  x : 0..1;
ASSIGN
  init(x) := 0;
  next(x) := 1;
""")

    result = smv_to_gc(model)
    output = format_gc_result_from_smv(model)

    assert len(result.commands) == 1
    assert "type x: 0..1" in output
    assert "init: x = 0" in output
    assert "x = 1" in output


def test_partition_lowering_compacts_same_update_vector_over_pc_ranges():
    model = parse_smv("""
MODULE main
VAR
  pc : 0..2;
  x : 0..2;
  y : 0..2;
ASSIGN
  init(pc) := 0;
  init(x) := 0;
  init(y) := 0;
  next(pc) := pc;
  next(x) :=
    case
      pc = 0 : 1;
      pc = 1 : 1;
      TRUE : 2;
    esac;
  next(y) :=
    case
      pc = 0 : 1;
      pc = 1 : 1;
      TRUE : 2;
    esac;
""")

    naive = smv_to_gc(model)
    partitioned = smv_to_gc(model, lowering_strategy="partition")

    assert len(naive.commands) == 3
    assert len(partitioned.commands) == 2
    assert (partitioned.commands[0].guards[0].left, partitioned.commands[0].guards[0].op) == (
        Var("pc"),
        CompOp.LE,
    )
    assert partitioned.commands[0].guards[0].right == Num(1)


def test_partition_lowering_accepts_explicit_control_variable_set():
    result = import_smv(
        """
MODULE main
VAR
  phase : 0..1;
  x : 0..1;
ASSIGN
  init(phase) := 0;
  init(x) := 0;
  next(phase) := phase;
  next(x) :=
    case
      phase = 0 : 0;
      TRUE : 1;
    esac;
""",
        lowering_strategy="partition",
        control_variables=("phase",),
    )

    assert len(result.commands) == 2
    assert all(command.guards[0].left == Var("phase") for command in result.commands)


def test_smv_cli_accepts_partition_lowering_option(tmp_path, capsys):
    smv_path = tmp_path / "model.smv"
    smv_path.write_text("""
MODULE main
VAR
  pc : 0..1;
  x : 0..1;
ASSIGN
  init(pc) := 0;
  init(x) := 0;
  next(pc) := pc;
  next(x) :=
    case
      pc = 0 : 0;
      TRUE : 1;
    esac;
""")

    exit_code = smv_cli_main([
        str(smv_path),
        "--smv-lowering",
        "partition",
        "--control-var",
        "pc",
    ])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "[] pc = 0 -> pc = pc; x = 0" in output
    assert "[] pc = 1 -> pc = pc; x = 1" in output


def test_smv_cli_accepts_supported_hyperltl(tmp_path, capsys):
    smv_path = tmp_path / "model.smv"
    hq_path = tmp_path / "property.hq"
    smv_path.write_text("""
MODULE main
VAR
  x : 0..1;
ASSIGN
  init(x) := 0;
  next(x) := x;
""")
    hq_path.write_text('forall A. forall B. G({"x"_A = "x"_B})')

    exit_code = smv_cli_main([str(smv_path), "--hyper", str(hq_path)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "HyperLTL property" in output
    assert "support: supported parser fragment" in output
    assert "self-composed guarded commands" in output
    assert "ap hq_ap_0" in output
    assert 'spec: "G(hq_ap_0)"' in output


def test_smv_cli_rejects_unsupported_hyperltl(tmp_path, capsys):
    smv_path = tmp_path / "model.smv"
    smv_path.write_text("""
MODULE main
VAR
  x : 0..1;
ASSIGN
  init(x) := 0;
  next(x) := x;
""")

    exit_code = smv_cli_main([
        str(smv_path),
        "--hyper-text",
        'forall A. exists B. G({"x"_A = "x"_B})',
    ])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "quantifier alternation" in captured.err


def test_smv_cli_rejects_hyperltl_unknown_smv_symbol(tmp_path, capsys):
    smv_path = tmp_path / "model.smv"
    smv_path.write_text("""
MODULE main
VAR
  x : 0..1;
ASSIGN
  init(x) := 0;
  next(x) := x;
""")

    exit_code = smv_cli_main([
        str(smv_path),
        "--hyper-text",
        'forall A. forall B. G({"missing"_A = "x"_B})',
    ])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "unknown SMV symbol" in captured.err
