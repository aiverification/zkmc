"""Parser for a small, explicit subset of NuSMV/SMV."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lark import Lark, Transformer

from .smv_types import (
    SmvAssignment,
    SmvBinary,
    SmvBool,
    SmvBooleanType,
    SmvCase,
    SmvCaseArm,
    SmvDefine,
    SmvEnumType,
    SmvExpr,
    SmvInt,
    SmvModel,
    SmvName,
    SmvRangeType,
    SmvSet,
    SmvType,
    SmvUnary,
    SmvVar,
)


_GRAMMAR_PATH = Path(__file__).parent / "smv_grammar.lark"


class _SmvTransformer(Transformer):
    def start(self, items: list[Any]) -> SmvModel:
        return items[0]

    def module(self, items: list[Any]) -> SmvModel:
        module_name = str(items[0])
        variables: list[SmvVar] = []
        defines: list[SmvDefine] = []
        assignments: list[SmvAssignment] = []

        for item in items[1:]:
            if item is None:
                continue
            section_name, section_items = item
            if section_name == "var":
                variables.extend(section_items)
            elif section_name == "define":
                defines.extend(section_items)
            elif section_name == "assign":
                assignments.extend(section_items)

        return SmvModel(
            module=module_name,
            variables=tuple(variables),
            defines=tuple(defines),
            assignments=tuple(assignments),
        )

    def module_args(self, items: list[Any]) -> None:
        return None

    def name_list(self, items: list[Any]) -> list[str]:
        return [str(item) for item in items]

    def section(self, items: list[Any]) -> Any:
        return items[0]

    def var_section(self, items: list[Any]) -> tuple[str, list[SmvVar]]:
        return ("var", list(items))

    def var_decl(self, items: list[Any]) -> SmvVar:
        name = str(items[0])
        type_def = items[1]
        return SmvVar(name=name, type=type_def)

    def boolean_type(self, items: list[Any]) -> SmvBooleanType:
        return SmvBooleanType()

    def range_type(self, items: list[Any]) -> SmvRangeType:
        return SmvRangeType(min_value=int(items[0]), max_value=int(items[1]))

    def enum_type(self, items: list[Any]) -> SmvEnumType:
        return SmvEnumType(tuple(str(value) for value in items[0]))

    def enum_values(self, items: list[Any]) -> list[Any]:
        return list(items)

    def enum_value(self, items: list[Any]) -> str | int:
        value = items[0]
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if hasattr(value, "type") and value.type == "SIGNED_INT":
            return int(value)
        return value

    def define_section(self, items: list[Any]) -> tuple[str, list[SmvDefine]]:
        return ("define", list(items))

    def define_decl(self, items: list[Any]) -> SmvDefine:
        return SmvDefine(name=str(items[0]), expr=items[1])

    def assign_section(self, items: list[Any]) -> tuple[str, list[SmvAssignment]]:
        return ("assign", list(items))

    def init_assign(self, items: list[Any]) -> SmvAssignment:
        return SmvAssignment(target=str(items[0]), expr=items[1], kind="init")

    def next_assign(self, items: list[Any]) -> SmvAssignment:
        return SmvAssignment(target=str(items[0]), expr=items[1], kind="next")

    def current_assign(self, items: list[Any]) -> SmvAssignment:
        return SmvAssignment(target=str(items[0]), expr=items[1], kind="current")

    def case_expr(self, items: list[Any]) -> SmvCase:
        return SmvCase(tuple(items))

    def case_arm(self, items: list[Any]) -> SmvCaseArm:
        return SmvCaseArm(guard=items[0], value=items[1])

    def or_expr(self, items: list[Any]) -> SmvBinary:
        return SmvBinary(op="|", left=items[0], right=items[1])

    def and_expr(self, items: list[Any]) -> SmvBinary:
        return SmvBinary(op="&", left=items[0], right=items[1])

    def op_binary(self, items: list[Any]) -> SmvBinary:
        return SmvBinary(op=str(items[1]), left=items[0], right=items[2])

    def not_expr(self, items: list[Any]) -> SmvUnary:
        return SmvUnary(op="!", expr=items[0])

    def neg_expr(self, items: list[Any]) -> SmvUnary:
        return SmvUnary(op="-", expr=items[0])

    def int_expr(self, items: list[Any]) -> SmvInt:
        return SmvInt(int(items[0]))

    def true_expr(self, items: list[Any]) -> SmvBool:
        return SmvBool(True)

    def false_expr(self, items: list[Any]) -> SmvBool:
        return SmvBool(False)

    def name_expr(self, items: list[Any]) -> SmvName:
        return SmvName(str(items[0]))

    def set_expr(self, items: list[Any]) -> SmvSet:
        values = items[0] if items else []
        return SmvSet(tuple(values))

    def expr_list(self, items: list[Any]) -> list[SmvExpr]:
        return list(items)

    def TRUE(self, token) -> bool:
        return True

    def FALSE(self, token) -> bool:
        return False


def create_smv_parser() -> Lark:
    return Lark(_GRAMMAR_PATH.read_text(), parser="lalr")


def parse_smv(text: str) -> SmvModel:
    """Parse SMV text into an SMV AST.

    This function only parses and structures the SMV input. It does not lower
    the model to guarded commands and it does not attach any property.
    """
    tree = create_smv_parser().parse(text)
    return _SmvTransformer().transform(tree)


def parse_smv_file(path: str) -> SmvModel:
    return parse_smv(Path(path).read_text())
