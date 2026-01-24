"""Encode guarded commands as matrix/vector inequality constraints.

Given a guarded command: [] guard -> assignments
We encode the transition relation as two matrix-vector pairs:
  - (A, b) for non-strict inequalities: Ax ≤ b  
  - (C, d) for strict inequalities: Cx < d

Where x = [vars, vars'] represents current and next-state variables.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple
import numpy as np
from numpy.typing import NDArray

from .ast_types import (
    GuardedCommand, Comparison, Assignment, CompOp,
    Expr, Var, Num, BinOp, Neg
)


@dataclass
class LinearExpr:
    """A linear expression: sum of (coefficient * variable) + constant.
    
    Represented as: coeffs[var] * var + ... + const
    """
    coeffs: Dict[str, int]  # variable name -> coefficient
    const: int  # constant term
    
    def __add__(self, other: "LinearExpr") -> "LinearExpr":
        result = dict(self.coeffs)
        for var, coeff in other.coeffs.items():
            result[var] = result.get(var, 0) + coeff
        return LinearExpr(coeffs=result, const=self.const + other.const)
    
    def __sub__(self, other: "LinearExpr") -> "LinearExpr":
        result = dict(self.coeffs)
        for var, coeff in other.coeffs.items():
            result[var] = result.get(var, 0) - coeff
        return LinearExpr(coeffs=result, const=self.const - other.const)
    
    def __neg__(self) -> "LinearExpr":
        return LinearExpr(
            coeffs={v: -c for v, c in self.coeffs.items()},
            const=-self.const
        )
    
    def scale(self, factor: int) -> "LinearExpr":
        return LinearExpr(
            coeffs={v: c * factor for v, c in self.coeffs.items()},
            const=self.const * factor
        )


def expr_to_linear(expr: Expr) -> LinearExpr:
    """Convert an AST expression to a LinearExpr.
    
    Raises ValueError if expression is not linear.
    """
    if isinstance(expr, Num):
        return LinearExpr(coeffs={}, const=expr.value)
    
    elif isinstance(expr, Var):
        return LinearExpr(coeffs={expr.name: 1}, const=0)
    
    elif isinstance(expr, Neg):
        return -expr_to_linear(expr.expr)
    
    elif isinstance(expr, BinOp):
        left = expr_to_linear(expr.left)
        right = expr_to_linear(expr.right)
        
        if expr.op == "+":
            return left + right
        elif expr.op == "-":
            return left - right
        elif expr.op == "*":
            # At least one side must be constant for linearity
            if not left.coeffs:
                return right.scale(left.const)
            elif not right.coeffs:
                return left.scale(right.const)
            else:
                raise ValueError(f"Non-linear multiplication: {expr}")
        else:
            raise ValueError(f"Unknown operator: {expr.op}")
    
    else:
        raise ValueError(f"Unknown expression type: {type(expr)}")


@dataclass
class Inequality:
    """A linear inequality: lhs ≤ rhs (is_strict=False) or lhs < rhs (is_strict=True).
    
    Normalized form: coeffs · x ≤ const (or < for strict)
    where coeffs[var] * var + ... ≤ const
    """
    coeffs: Dict[str, int]
    const: int
    is_strict: bool  # True for <, False for ≤
    
    def to_nonstrict(self) -> "Inequality":
        """Convert strict inequality to non-strict using integer semantics.
        
        x < c  becomes  x ≤ c - 1
        x ≤ c  stays    x ≤ c
        """
        if self.is_strict:
            return Inequality(coeffs=self.coeffs, const=self.const - 1, is_strict=False)
        return self
    
    def __repr__(self) -> str:
        terms = []
        for var, coeff in sorted(self.coeffs.items()):
            if coeff == 0:
                continue
            if coeff == 1:
                terms.append(var)
            elif coeff == -1:
                terms.append(f"-{var}")
            else:
                terms.append(f"{coeff}*{var}")
        lhs = " + ".join(terms) if terms else "0"
        op = "<" if self.is_strict else "≤"
        return f"{lhs} {op} {self.const}"


def comparison_to_inequalities(comp: Comparison, primed: bool = False) -> List[Inequality]:
    """Convert a comparison to one or two inequalities.
    
    Args:
        comp: The comparison to convert
        primed: If True, add ' suffix to all variables (for next-state)
    
    Returns:
        List of inequalities (equalities produce two)
    """
    left = expr_to_linear(comp.left)
    right = expr_to_linear(comp.right)
    
    # Apply priming if needed
    if primed:
        left = LinearExpr(
            coeffs={f"{v}'": c for v, c in left.coeffs.items()},
            const=left.const
        )
        right = LinearExpr(
            coeffs={f"{v}'": c for v, c in right.coeffs.items()},
            const=right.const
        )
    
    # Normalize to: left - right ≤ 0 or left - right < 0
    # i.e., coeffs · x ≤ const where const = right.const - left.const
    diff = left - right
    
    if comp.op == CompOp.LT:
        # left < right  =>  left - right < 0  =>  coeffs · x < -const
        return [Inequality(coeffs=diff.coeffs, const=-diff.const, is_strict=True)]
    
    elif comp.op == CompOp.LE:
        # left ≤ right  =>  left - right ≤ 0  =>  coeffs · x ≤ -const
        return [Inequality(coeffs=diff.coeffs, const=-diff.const, is_strict=False)]
    
    elif comp.op == CompOp.EQ:
        # left = right  =>  left ≤ right AND left ≥ right
        return [
            Inequality(coeffs=diff.coeffs, const=-diff.const, is_strict=False),
            Inequality(coeffs=(-diff).coeffs, const=diff.const, is_strict=False),
        ]
    
    elif comp.op == CompOp.GE:
        # left ≥ right  =>  right - left ≤ 0  =>  right ≤ left
        neg_diff = -diff
        return [Inequality(coeffs=neg_diff.coeffs, const=-neg_diff.const, is_strict=False)]
    
    elif comp.op == CompOp.GT:
        # left > right  =>  right < left  =>  right - left < 0
        neg_diff = -diff
        return [Inequality(coeffs=neg_diff.coeffs, const=-neg_diff.const, is_strict=True)]
    
    else:
        raise ValueError(f"Unknown comparison operator: {comp.op}")


def assignment_to_inequalities(assign: Assignment) -> List[Inequality]:
    """Convert an assignment var = expr to equalities var' = expr.
    
    This encodes: var' = expr (where expr uses unprimed variables)
    As: var' - expr = 0  =>  var' - expr ≤ 0 AND expr - var' ≤ 0
    
    Rearranging: (coeffs of var' - expr) · x ≤ (constant from expr)
    """
    expr_lin = expr_to_linear(assign.expr)
    
    # var' = expr  =>  var' - expr = 0
    # Rearranged: (var' - linear_part) = const
    # coeffs: var' has coeff 1, expr variables have negated coeffs
    coeffs1: Dict[str, int] = {f"{assign.var}'": 1}
    for v, c in expr_lin.coeffs.items():
        coeffs1[v] = coeffs1.get(v, 0) - c
    # var' - linear_part ≤ const  AND  var' - linear_part ≥ const
    # i.e., coeffs1 · x ≤ const  AND  -coeffs1 · x ≤ -const
    const1 = expr_lin.const
    
    # Negated version for the other direction
    coeffs2 = {v: -c for v, c in coeffs1.items()}
    const2 = -const1
    
    return [
        Inequality(coeffs=coeffs1, const=const1, is_strict=False),
        Inequality(coeffs=coeffs2, const=const2, is_strict=False),
    ]


def identity_constraints(variables: List[str], assigned_vars: set[str]) -> List[Inequality]:
    """Generate identity constraints var' = var for unassigned variables.
    
    For variables not explicitly assigned, we need var' = var to ensure
    they stay unchanged in the transition.
    """
    inequalities = []
    for var in variables:
        if var not in assigned_vars:
            # var' = var  =>  var' - var ≤ 0 AND -var' + var ≤ 0
            inequalities.append(Inequality(
                coeffs={f"{var}'": 1, var: -1},
                const=0,
                is_strict=False
            ))
            inequalities.append(Inequality(
                coeffs={f"{var}'": -1, var: 1},
                const=0,
                is_strict=False
            ))
    return inequalities


@dataclass
class InitEncoding:
    """Encoded initial condition as matrix-vector pair.

    A_0 x ≤ b_0

    where x = [var1, var2, ...] represents current-state variables only.
    """
    variables: List[str]  # ordered list of variable names
    A_0: NDArray[np.int64]  # coefficient matrix for initial condition
    b_0: NDArray[np.int64]  # constant vector for initial condition

    def __repr__(self) -> str:
        lines = [f"Variables: {self.variables}"]
        if self.A_0.shape[0] > 0:
            lines.append(f"\nA_0 x ≤ b_0 where:")
            lines.append(f"A_0 =\n{self.A_0}")
            lines.append(f"b_0 = {self.b_0}")
        else:
            lines.append("\nNo constraints (always true)")
        return "\n".join(lines)


def encode_init(
    guards: List[Comparison],
    variables: List[str] | None = None
) -> InitEncoding:
    """Encode initial condition guards as matrix-vector constraints.

    Args:
        guards: List of comparison constraints for initial condition
        variables: Optional ordered list of variables. If None, extracted from guards.

    Returns:
        InitEncoding with (A_0, b_0) for initial condition
    """
    # Extract variables from guards if not provided
    if variables is None:
        vars_set: set[str] = set()

        def collect_vars(e: Expr) -> None:
            if isinstance(e, Var):
                vars_set.add(e.name)
            elif isinstance(e, BinOp):
                collect_vars(e.left)
                collect_vars(e.right)
            elif isinstance(e, Neg):
                collect_vars(e.expr)

        for guard in guards:
            collect_vars(guard.left)
            collect_vars(guard.right)

        variables = sorted(vars_set)

    # Encode guards to inequalities (no primed variables)
    all_ineqs: List[Inequality] = []
    for guard in guards:
        all_ineqs.extend(comparison_to_inequalities(guard, primed=False))

    # Convert to non-strict inequalities only (init conditions don't need strict)
    all_ineqs = [iq.to_nonstrict() for iq in all_ineqs]

    # Build variable index
    var_idx = {v: i for i, v in enumerate(variables)}
    n_vars = len(variables)

    # Build matrix
    if all_ineqs:
        m = len(all_ineqs)
        A_0 = np.zeros((m, n_vars), dtype=np.int64)
        b_0 = np.zeros(m, dtype=np.int64)

        for i, iq in enumerate(all_ineqs):
            for v, coeff in iq.coeffs.items():
                if v in var_idx:
                    A_0[i, var_idx[v]] = coeff
            b_0[i] = iq.const
    else:
        # No constraints (always true)
        A_0 = np.zeros((0, n_vars), dtype=np.int64)
        b_0 = np.zeros(0, dtype=np.int64)

    return InitEncoding(variables=variables, A_0=A_0, b_0=b_0)


@dataclass
class TransitionEncoding:
    """Encoded transition relation as matrix-vector pairs.
    
    Ax ≤ b  (non-strict inequalities)
    Cx < d  (strict inequalities)
    
    where x = [var1, var2, ..., var1', var2', ...]
    """
    variables: List[str]  # ordered list of variable names (unprimed)
    A: NDArray[np.int64]  # coefficient matrix for ≤ constraints
    b: NDArray[np.int64]  # constant vector for ≤ constraints
    C: NDArray[np.int64]  # coefficient matrix for < constraints
    d: NDArray[np.int64]  # constant vector for < constraints
    
    def full_variables(self) -> List[str]:
        """Get full variable list including primed versions."""
        return self.variables + [f"{v}'" for v in self.variables]
    
    def __repr__(self) -> str:
        lines = [f"Variables: {self.full_variables()}"]
        if self.A.shape[0] > 0:
            lines.append(f"\nAx ≤ b where:")
            lines.append(f"A =\n{self.A}")
            lines.append(f"b = {self.b}")
        if self.C.shape[0] > 0:
            lines.append(f"\nCx < d where:")
            lines.append(f"C =\n{self.C}")
            lines.append(f"d = {self.d}")
        return "\n".join(lines)


def encode_transition(
    cmd: GuardedCommand,
    variables: List[str] | None = None,
    nonstrict_only: bool = False,
) -> TransitionEncoding:
    """Encode a guarded command as matrix-vector inequality constraints.
    
    Args:
        cmd: The guarded command to encode
        variables: Optional ordered list of variables. If None, extracted from command.
        nonstrict_only: If True, convert all strict inequalities to non-strict
                        using integer semantics (x < c → x ≤ c-1)
    
    Returns:
        TransitionEncoding with (A, b) for ≤ and (C, d) for < constraints
    """
    # Get variables
    if variables is None:
        variables = sorted(cmd.get_variables())
    
    # Collect all inequalities
    all_ineqs: List[Inequality] = []
    
    # Guards (use unprimed variables)
    for guard in cmd.guards:
        all_ineqs.extend(comparison_to_inequalities(guard, primed=False))
    
    # Assignments (var' = expr where expr uses unprimed vars)
    assigned_vars = {a.var for a in cmd.assignments}
    for assign in cmd.assignments:
        all_ineqs.extend(assignment_to_inequalities(assign))
    
    # Identity constraints for unassigned variables
    all_ineqs.extend(identity_constraints(variables, assigned_vars))
    
    # Build variable index: [var1, var2, ..., var1', var2', ...]
    full_vars = variables + [f"{v}'" for v in variables]
    var_idx = {v: i for i, v in enumerate(full_vars)}
    n_vars = len(full_vars)
    
    # Handle non-strict only mode
    if nonstrict_only:
        all_ineqs = [iq.to_nonstrict() for iq in all_ineqs]
    
    # Separate strict and non-strict
    strict_ineqs = [iq for iq in all_ineqs if iq.is_strict]
    nonstrict_ineqs = [iq for iq in all_ineqs if not iq.is_strict]
    
    # Build matrices
    def build_matrix(ineqs: List[Inequality]) -> Tuple[NDArray[np.int64], NDArray[np.int64]]:
        if not ineqs:
            return np.zeros((0, n_vars), dtype=np.int64), np.zeros(0, dtype=np.int64)
        
        m = len(ineqs)
        mat = np.zeros((m, n_vars), dtype=np.int64)
        vec = np.zeros(m, dtype=np.int64)
        
        for i, iq in enumerate(ineqs):
            for v, coeff in iq.coeffs.items():
                if v in var_idx:
                    mat[i, var_idx[v]] = coeff
            vec[i] = iq.const
        
        return mat, vec
    
    A, b = build_matrix(nonstrict_ineqs)
    C, d = build_matrix(strict_ineqs)
    
    return TransitionEncoding(variables=variables, A=A, b=b, C=C, d=d)


def encode_program(
    commands: List[GuardedCommand],
    nonstrict_only: bool = False,
) -> List[TransitionEncoding]:
    """Encode multiple guarded commands with consistent variable ordering.
    
    Args:
        commands: List of guarded commands to encode
        nonstrict_only: If True, convert all strict inequalities to non-strict
    
    Returns:
        One TransitionEncoding per guarded command.
    """
    # Collect all variables from all commands
    all_vars: set[str] = set()
    for cmd in commands:
        all_vars.update(cmd.get_variables())
    
    variables = sorted(all_vars)
    
    return [encode_transition(cmd, variables, nonstrict_only) for cmd in commands]
