# The `.gc` language

This document describes the input language accepted by `zkterm`, `zkrank`, `zkverify`, `zkfarkas`, and `zkexplicit`. A single `.gc` file combines a program (as guarded commands), its Büchi automaton, a ranking function, initial conditions, and any supporting constants or type declarations.

The grammar is defined in [`src/zkterm_tool/grammar.lark`](src/zkterm_tool/grammar.lark) — that file is the authoritative reference.

## File structure

A `.gc` file is a sequence of top-level declarations. Order is free and most combinations can coexist in one file:

| Construct | Form | Purpose |
|-----------|------|---------|
| Constant | `const NAME = expr` | Named integer value (folded at parse time). |
| Type annotation | `type var: min..max` | Declare a variable's bounds. Injected into init + commands automatically. |
| Initial condition | `init: guard` | Constraints on the initial state. |
| Guarded command | `[] guard -> assignments` | A program transition. |
| Ranking function | `rank(state): cases…` | Piecewise linear termination measure per automaton state. |
| Automaton transition | `trans(q, q'): guard` / `trans!(q, q'): guard` | Büchi automaton edge; `!` marks it as fair/accepting. |
| Automaton init | `automaton_init: q0, q1, …` | Starting automaton states (required by `zkexplicit`). |

A minimal but complete example (see [`examples/example.gc`](examples/example.gc)):

```
const z = 10

init: y = 0 && success = 0

[] y < z -> y = y + 1
[] y == z -> success = 1

automaton_init: q0
trans!(q0, q0): success == 0

rank(q0):
    [] y <= z -> z - y + 1 - success
```

## Constants

```
const name = expr
```

- `expr` is an integer-valued arithmetic expression: `+`, `-`, `*`, `**` (power), unary `-`, parentheses.
- A constant may reference previously-defined constants.
- All constant expressions are evaluated at parse time, so `const maxDelay = 2**(maxAttempts - 1) * initialDelay` becomes a single integer.
- Any constant can be overridden from the command line via `--const NAME=VALUE`, applied before evaluation, so computed constants respect the override.

Example:

```
const maxAttempts = 3
const initialDelay = 64
const maxDelay = 2**(maxAttempts - 1) * initialDelay  // evaluated to 256
```

## Type annotations

```
type var: min..max
```

Both bounds are constant expressions.

Type annotations declare the intended domain of a variable and **auto-inject bounds** into:

- every initial condition, and
- every guarded command's guard.

They are **not** injected into ranking-function guards or automaton-transition guards — those contexts intentionally reason about unreachable or symbolic regions (for example, `[] x < 0 -> inf` must remain expressible).

Example:

```
const maxVal = 10
type x: 0..maxVal

init: x = 0             // becomes: x = 0 && x >= 0 && x <= maxVal
[] x < maxVal -> x = x + 1  // guard becomes: x < maxVal && x >= 0 && x <= maxVal
```

`zkexplicit` also uses type annotations as the default `--bounds`, so you can usually omit `--bounds` on the command line.

## Initial conditions

```
init: guard
```

- The guard is a conjunction of comparisons over current-state variables only (no primed variables).
- `true` is accepted as an unrestricted initial condition.
- Strict inequalities are converted to non-strict form under integer semantics (`x < c` → `x ≤ c - 1`).

Examples:

```
init: x = 0 && y >= 0 && y < 10
init: true
```

## Guarded commands

```
[] guard -> assignments
```

- **Guard**: conjunction of comparisons (`<`, `<=`/`≤`, `=`/`==`, `>=`/`≥`, `>`), joined with `&&` or `∧`. The literal `true` is also allowed.
- **Assignments**: one or more `var = expr`, separated by `;` (or `,`). A trailing `;` is allowed.
- **Unassigned variables** implicitly keep their value: `var' = var` is inserted automatically.
- **Expressions** are linear in the program variables: multiplication is only allowed when at least one side is constant. `2**x` where `x` is a variable is rejected.
- **Integer semantics**: strict inequalities in guards become non-strict (`x < c` → `x ≤ c - 1`).

Examples:

```
[] x < maxVal -> x = x + 1
[] x >= maxVal -> x = 0; y = y + 1
[] status == Init -> status = WaitOFF; delay = k0
```

## Ranking functions

```
rank(state):
    [] guard -> expr
    [] guard -> expr
    [] guard -> inf
    …
```

Each case is `[] guard -> expr` (a finite case) or `[] guard -> inf` (an infinity case marking a region where the ranking is undefined).

- **First-match** semantics: at runtime, the first case whose guard is satisfied determines the value.
- **Coverage**: every state must be covered by at least one case, finite or infinite. This is validated by `zkverify` and `zkrank` unless `--skip-validation` is passed.
- The expression is linear in the current-state variables.

Multi-case example:

```
rank(q0):
  [] x >= 0 && x < maxVal -> maxVal - x     // finite
  [] x >= maxVal -> 1                       // finite
  [] x < 0 -> inf                           // infinity: unreachable region
```

Infinity cases are how you document which parts of the state space the ranking does not need to decrease on.

## Büchi automaton transitions

```
trans(q, q'): guard        // regular transition
trans!(q, q'): guard       // fair (accepting) transition
automaton_init: q0, q1, …  // initial automaton states
```

- Regular transitions belong to the transition relation δ only.
- Fair transitions (marked with `!`) belong to both δ and the acceptance set F. They enforce strict decrease of the ranking function during verification (ζ = 1); regular transitions require only non-increase (ζ = 0).
- Guards are over current-state variables only — type bounds are not auto-injected here.
- `automaton_init` is a comma-separated list of starting states. It is required by `zkexplicit`; `zkverify` falls back to "every state with a ranking function" when it is omitted.

Examples:

```
automaton_init: q0
trans(q0, q1): x >= 0 && x < 5
trans!(q1, q1): x > 0 && x < maxVal
trans(q1, q0): x >= maxVal
```

A self-loop that is always enabled — useful as a degenerate automaton:

```
automaton_init: q0
trans(q0, q0): true
```

## Keywords and operators

| Token | Meaning |
|-------|---------|
| `true` | Unconstrained guard (no constraints). |
| `inf` | Infinity value in a ranking case. |
| `<`, `<=`, `≤` | Less than / less-or-equal. |
| `=`, `==` | Equality (produces two opposing inequalities). |
| `>=`, `≥`, `>` | Greater-or-equal / greater. |
| `&&`, `∧` | Conjunction in guards (guards are always conjunctive). |
| `+`, `-`, `*`, `**` | Arithmetic. `*` and `**` require a constant operand — expressions must be linear. |
| `//` | Single-line comment. |

Unicode variants (`≤`, `≥`, `∧`) are interchangeable with their ASCII forms.

## A realistic example

The [`examples/dhcp.gc`](examples/dhcp.gc) file models a DHCP client as a guarded-command program with:

- Seven named protocol states (`Init`, `WaitOFF`, `Offered`, `WaitAN`, `TryARP`, `WaitD`, `Configured`, `Fail`).
- Type annotations on every variable so bounds are not duplicated.
- A ranking function with 11 finite cases and 14 infinity cases — the infinity cases document unreachable combinations of `status`, `i`, and `delay`.
- A single self-looping Büchi automaton (`trans(q0, q0): true`).

See the rest of [`examples/`](examples/) for more complete programs, and [`examples/README.md`](examples/README.md) for a short tour.

## Appendix: grammar

The full grammar lives in [`src/zkterm_tool/grammar.lark`](src/zkterm_tool/grammar.lark). A condensed version:

```
start              : (const_def | type_def | init_condition
                    | guarded_command | ranking_function
                    | automaton_trans | automaton_init)*

const_def          : "const" NAME "=" const_expr
type_def           : "type" VAR ":" const_expr ".." const_expr
init_condition     : "init" ":" guard
guarded_command    : "[]" guard "->" assignments
ranking_function   : "rank" "(" STATE ")" ":" ranking_case+
ranking_case       : "[]" guard "->" (expr | "inf")
automaton_trans    : "trans" "!"? "(" STATE "," STATE ")" ":" guard
automaton_init     : "automaton_init" ":" STATE ("," STATE)*

guard              : "true" | comparison (("&&" | "∧") comparison)*
comparison         : expr COMP_OP expr
COMP_OP            : "<" | "<=" | "≤" | "=" | "==" | ">=" | "≥" | ">"

assignments        : assignment (";" assignment)* ";"?
assignment         : VAR "=" expr

expr               : expr "+" term | expr "-" term | term
term               : term "*" factor | factor
factor             : "-" factor | power
power              : atom "**" factor | atom
atom               : NUMBER | VAR | "(" expr ")"

NUMBER             : /[0-9]+/
NAME, VAR, STATE   : /[a-zA-Z_][a-zA-Z_0-9]*/

// Comments: `// …` to end of line
```
