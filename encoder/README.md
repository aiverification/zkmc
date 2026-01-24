# zkterm-tool

A Python tool that transforms guarded commands and ranking functions into matrix/vector inequality forms for formal verification and termination analysis.

## Overview

Given a guarded command program like:

```
[] y < z -> y = y + 1
```

The tool encodes the transition relation as two matrix-vector pairs:

- **(A, b)** for non-strict inequalities: $Ax \leq b$
- **(C, d)** for strict inequalities: $Cx < d$

Where $x = [vars, vars']$ contains current and next-state variables.

## Installation

```bash
# Clone and install with uv
git clone <repo-url>
cd zkterm-tool
uv sync
```

## Usage

### Command Line

```bash
# From stdin
echo '[] y < z -> y = y + 1' | zkterm

# From file
zkterm program.gc

# Verbose mode (shows parsed AST)
zkterm -v program.gc

# Symbolic output (human-readable inequalities)
zkterm -s program.gc

# Non-strict mode (convert x < c to x ≤ c-1)
zkterm -n program.gc

# Combine options
zkterm -vsn program.gc
```

### CLI Options

| Option | Long | Description |
|--------|------|-------------|
| `-v` | `--verbose` | Show parsed commands before encoding |
| `-s` | `--symbolic` | Output inequalities with variable names (e.g., `2x - x' <= 2`) |
| `-n` | `--non-strict` | Convert strict inequalities to non-strict using integer semantics (`x < c` → `x ≤ c-1`) |

### Example

Input:
```
[] y < z -> y = y + 1
```

Matrix output (`zkterm`):
```
Variables x = [y, z, y', z']

Non-strict inequalities Ax ≤ b:
A =
  [ -1   0   1   0]
  [  1   0  -1   0]
  [  0  -1   0   1]
  [  0   1   0  -1]
b = [ -1   1   0   0]

Strict inequalities Cx < d:
C =
  [  1  -1   0   0]
d = [  0]
```

Symbolic output (`zkterm -s`):
```
Variables x = [y, z, y', z']

Non-strict inequalities Ax ≤ b:
  -y + y' <= 1
  y - y' <= -1
  -z + z' <= 0
  z - z' <= 0

Strict inequalities Cx < d:
  y - z < 0
```

Non-strict mode (`zkterm -sn`):
```
Variables x = [y, z, y', z']

Non-strict inequalities Ax ≤ b:
  y - z <= -1
  -y + y' <= 1
  y - y' <= -1
  -z + z' <= 0
  z - z' <= 0

No strict inequalities
```

### Constants

Define named constants for readability:

```
const received = 1
const wait = 0
const success = 1

[] ack = received && status = wait -> status = success
```

Constants are substituted at parse time. Comments (starting with `//`) are also supported.

### Multiple Transitions

The tool handles multiple guarded commands, producing separate encodings:

```
[] x < 10 -> x = x + 1
[] x >= 10 -> x = 0
```

## Ranking Functions (zkrank)

The `zkrank` tool encodes piecewise linear ranking functions V(x, q) used in termination analysis.

### Command Line

```bash
# From stdin
echo 'rank(q0): [] x > 0 -> x' | zkrank

# From file
zkrank program.gc

# Verbose mode (shows parsed ranking functions)
zkrank -v program.gc

# Symbolic output (human-readable)
zkrank -s program.gc
```

### Syntax

Define ranking functions for automaton states:

```
rank(q0):
  [] x >= 0 && x < 10 -> 10 - x
  [] x >= 10 -> 1
```

Each case has:
- **Guard**: Conjunction of linear inequalities (like guarded commands)
- **Expression**: Linear expression computing the ranking value

**Semantics**: Cases are checked in order (first-match). If no guard is satisfied, V(x, q) = +∞.

### Example

Input:
```
const maxVal = 10

rank(q0):
  [] x >= 0 && x < maxVal -> maxVal - x
  [] x >= maxVal -> 1
```

Matrix output (`zkrank program.gc`):
```
=== Ranking Function for State q0 ===
Variables: [x]

Case 1:
  Guard A_j x <= b_j:
    A_j =
      [ -1]
      [  1]
    b_j = [  0  10]
  Expression C_j x + d_j:
    C_j = [ -1]
    d_j = 10

Case 2:
  Guard A_j x <= b_j:
    A_j =
      [ -1]
    b_j = [-10]
  Expression C_j x + d_j:
    C_j = [  0]
    d_j = 1
```

Symbolic output (`zkrank -s program.gc`):
```
=== Ranking Function for State q0 ===
Variables: [x]

Case 1:
  Guard:
    -x <= 0
    x <= 10
  Expression: -x +10

Case 2:
  Guard:
    -x <= -10
  Expression: 1
```

### Mixed Files

Both transitions and ranking functions can coexist in the same `.gc` file:

```
// Transitions
[] x < 10 -> x = x + 1
[] x >= 10 -> x = 0

// Ranking function
rank(q0):
  [] x >= 0 && x < 10 -> 10 - x
  [] x >= 10 -> 1
```

Use `zkterm` to encode only transitions, `zkrank` to encode only ranking functions.

### Python API (Ranking)

```python
from zkterm_tool import parse_with_constants, encode_ranking_functions

# Parse file with ranking functions
result = parse_with_constants(text)

# Encode ranking functions
encodings = encode_ranking_functions(result.ranking_functions)

for state, enc in encodings.items():
    print(f"State: {state}")
    for i, case in enumerate(enc.cases):
        print(f"  Case {i+1}: A_j={case.A_j}, b_j={case.b_j}")
        print(f"           C_j={case.C_j}, d_j={case.d_j}")
```

### Python API

```python
from zkterm_tool import parse, encode_program

# Parse guarded commands
commands = parse("[] y < z -> y = y + 1")

# Encode to matrices
encodings = encode_program(commands)

for enc in encodings:
    print(f"Variables: {enc.full_variables()}")
    print(f"A = {enc.A}, b = {enc.b}")  # non-strict: Ax ≤ b
    print(f"C = {enc.C}, d = {enc.d}")  # strict: Cx < d
```

## Syntax

### Constants

```
const name = value
```

Constants are substituted into expressions at parse time.

### Comments

```
// This is a comment
const x = 1  // inline comment
```

### Guarded Commands

```
[] guard -> assignments
```

- **Guard**: Conjunction of comparisons (`<`, `<=`, `=`, `>=`, `>`)
- **Assignments**: Semicolon-separated variable updates

### Ranking Functions

```
rank(state_name):
  [] guard -> expression
  [] guard -> expression
  ...
```

- **State name**: Identifier for automaton state (e.g., `q0`, `q1`)
- **Guard**: Conjunction of comparisons (same as guarded commands)
- **Expression**: Linear arithmetic expression
- **Semantics**: First-match (cases checked in order)

### Operators

| Operator | Meaning |
|----------|---------|
| `<` | Strict less than |
| `<=`, `≤` | Less than or equal |
| `=`, `==` | Equality |
| `>=`, `≥` | Greater than or equal |
| `>` | Strict greater than |
| `&&`, `∧` | Conjunction in guards |

### Expressions

Linear arithmetic expressions with `+`, `-`, `*` (multiplication by constants only).

## Encoding Details

### Transition Encoding (zkterm)

The encoding uses integer semantics:

1. **Guards** become constraints on current-state variables
2. **Assignments** `var' = expr` become equality constraints (encoded as two inequalities)
3. **Unassigned variables** get identity constraints: `var' = var`

Strict inequalities (`<`, `>`) are separated into the $(C, d)$ pair, while non-strict inequalities (`<=`, `>=`, `=`) go into $(A, b)$.

### Ranking Function Encoding (zkrank)

For each case j in ranking function V(x, q):

1. **Guard** `A_j x ≤ b_j` encodes the condition for this case
   - Comparisons are converted to inequalities
   - Multiple comparisons in conjunction become multiple rows in A_j

2. **Expression** `C_j x + d_j` encodes the ranking value
   - C_j is a row vector of variable coefficients
   - d_j is the constant term
   - For expression `2x + 3y - 1`: C_j = [2, 3], d_j = -1

Cases are ordered (first satisfied guard determines the value). If no guard is satisfied, V(x, q) = +∞.

## Development

```bash
# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=zkterm_tool
```

## License

MIT
