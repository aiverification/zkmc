# zkterm-tool

A Python tool that transforms guarded commands into matrix/vector inequality form for encoding transition relations.

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

The encoding uses integer semantics:

1. **Guards** become constraints on current-state variables
2. **Assignments** `var' = expr` become equality constraints (encoded as two inequalities)
3. **Unassigned variables** get identity constraints: `var' = var`

Strict inequalities (`<`, `>`) are separated into the $(C, d)$ pair, while non-strict inequalities (`<=`, `>=`, `=`) go into $(A, b)$.

## Development

```bash
# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=zkterm_tool
```

## License

MIT
