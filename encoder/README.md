# zkterm-tool

A Python tool that transforms guarded commands and ranking functions into matrix/vector inequality forms for formal verification and termination analysis.

## Overview

This tool provides encoding for three types of formal specifications:

1. **Initial Conditions**: Encode initial state constraints as $A_0 x \leq b_0$
2. **Program Transitions** (guarded commands): Encode state transitions as matrix inequalities
3. **Ranking Functions**: Encode termination measures as piecewise linear functions
4. **Büchi Automaton Transitions**: Encode automaton transitions with fair/accepting states

### Program Transitions

Given a guarded command program like:

```
[] y < z -> y = y + 1
```

The tool encodes the transition relation as a matrix-vector pair **(A, b)** where $Ax \leq b$.

All strict inequalities are automatically converted to non-strict form using integer semantics (e.g., `x < c` becomes `x ≤ c-1`).

The variable vector $x = [vars, vars']$ contains current and next-state variables.

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

# Combine options
zkterm -vs program.gc
```

### CLI Options

| Option | Long | Description |
|--------|------|-------------|
| `-v` | `--verbose` | Show parsed commands before encoding |
| `-s` | `--symbolic` | Output inequalities with variable names (e.g., `2x - x' <= 2`) |

**Note:** Strict inequalities are automatically converted to non-strict using integer semantics (`x < c` → `x ≤ c-1`).

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
  [  1  -1   0   0]
  [ -1   0   1   0]
  [  1   0  -1   0]
  [  0  -1   0   1]
  [  0   1   0  -1]
b = [ -1   1  -1   0   0]

No strict inequalities
```

Symbolic output (`zkterm -s`):
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

Use `zkterm` to encode transitions, initial conditions, and automaton transitions. Use `zkrank` to encode ranking functions.

## Initial Conditions

The `zkterm` tool also encodes initial state constraints.

### Syntax

Define initial conditions using the `init` keyword:

```
init: x = 0 && y >= 0 && y < 10
```

The initial condition specifies constraints on the initial state using the same guard syntax as guarded commands.

### Example

Input:
```
const maxVal = 10

init: x = 0 && y >= 0 && y < maxVal

[] x < maxVal -> x = x + 1
[] x >= maxVal -> x = 0
```

Matrix output (`zkterm program.gc`):
```
=== Initial Condition ===
Variables: [x, y]

A_0 x <= b_0:
  A_0 =
    [  1   0]
    [ -1   0]
    [  0  -1]
    [  0   1]
  b_0 = [  0   0   0   9]

=== Transition 1 ===
...
```

Symbolic output (`zkterm -s program.gc`):
```
=== Initial Condition ===
Variables: [x, y]

A_0 x <= b_0:
  x <= 0
  -x <= 0
  -y <= 0
  y <= 9

=== Transition 1 ===
...
```

## Büchi Automaton Transitions

The `zkterm` tool encodes Büchi automaton transitions with support for fair (accepting) transitions.

### Syntax

Define automaton transitions:

```
trans(q0, q1): x >= 0 && x < 5       // Regular transition
trans!(q1, q1): x > 0 && x < 10      // Fair transition (marked with !)
trans(q1, q0): x >= 10               // Regular transition
```

- **Regular transition** `trans(q, q')`: Encodes as δ transition only
- **Fair transition** `trans!(q, q')`: Encodes as both δ and F (accepting) transition

### Example

Input:
```
const maxVal = 10

init: x = 0

[] x < maxVal -> x = x + 1

trans(q0, q1): x >= 0 && x < 5
trans!(q1, q1): x > 0 && x < maxVal
trans(q1, q0): x >= maxVal
```

Matrix output (`zkterm program.gc`):
```
=== Automaton Transitions ===
Variables: [x]

Transition: q0 -> q1
  δ encoding A^(q0,q1) x <= b:
    A =
      [ -1]
      [  1]
    b = [  0   4]
  Fair: NO

Transition: q1 -> q1 (FAIR)
  δ encoding A^(q1,q1) x <= b:
    A =
      [ -1]
      [  1]
    b = [ -1   9]
  F encoding A^(q1,q1) x <= b:
    (same as δ)
  Fair: YES

Transition: q1 -> q0
  δ encoding A^(q1,q0) x <= b:
    A =
      [ -1]
    b = [-10]
  Fair: NO
```

Symbolic output (`zkterm -s program.gc`):
```
=== Automaton Transitions ===
Variables: [x]

Transition: q0 -> q1
  Guard: -x <= 0 && x <= 4
  δ encoding A^(q0,q1) x <= b:
    -x <= 0
    x <= 4
  Fair: NO

Transition: q1 -> q1 (FAIR)
  Guard: -x <= -1 && x <= 9
  δ encoding A^(q1,q1) x <= b:
    -x <= -1
    x <= 9
  F encoding A^(q1,q1) x <= b:
    (same as δ)
  Fair: YES
...
```

### Unified File Format

All components can coexist in a single `.gc` file:

```
// Constants
const maxVal = 10

// Initial condition
init: x = 0 && y = 0

// Program transitions (guarded commands)
[] x < maxVal -> x = x + 1
[] x >= maxVal -> x = 0; y = y + 1

// Ranking functions
rank(q0):
  [] x >= 0 && x < maxVal -> maxVal - x
  [] x >= maxVal -> 1

// Büchi automaton transitions
trans(q0, q1): x >= 0 && x < 5
trans!(q1, q1): x > 0 && x < maxVal
trans(q1, q0): x >= maxVal
```

The `zkterm` tool will output all relevant sections (init, transitions, automaton) when they are present in the file.

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

#### Transitions
```python
from zkterm_tool import parse, encode_program

# Parse guarded commands
commands = parse("[] y < z -> y = y + 1")

# Encode to matrices (strict inequalities automatically converted to non-strict)
encodings = encode_program(commands, nonstrict_only=True)

for enc in encodings:
    print(f"Variables: {enc.full_variables()}")
    print(f"A = {enc.A}, b = {enc.b}")  # Ax ≤ b (all inequalities)
```

#### Initial Conditions
```python
from zkterm_tool import parse_with_constants, encode_init

# Parse with init condition
result = parse_with_constants("init: x = 0 && y >= 0")

# Encode initial condition
if result.init_condition:
    init_enc = encode_init(result.init_condition)
    print(f"Variables: {init_enc.variables}")
    print(f"A_0 = {init_enc.A_0}")
    print(f"b_0 = {init_enc.b_0}")
```

#### Büchi Automaton Transitions
```python
from zkterm_tool import parse_with_constants, encode_automaton_transitions

# Parse automaton transitions
result = parse_with_constants("""
    trans(q0, q1): x >= 0 && x < 5
    trans!(q1, q0): x >= 10
""")

# Encode automaton transitions
aut_encodings = encode_automaton_transitions(result.automaton_transitions)

for enc in aut_encodings:
    print(f"Transition: {enc.from_state} -> {enc.to_state}")
    print(f"Fair: {enc.is_fair}")
    print(f"δ: A_delta = {enc.A_delta}, b_delta = {enc.b_delta}")
    if enc.is_fair:
        print(f"F: A_fair = {enc.A_fair}, b_fair = {enc.b_fair}")
```

## Verification (zkverify)

The `zkverify` tool verifies termination obligations for guarded command programs using Farkas lemma and Z3 SMT solver.

### Command Line

```bash
# Verify a program
zkverify program.gc

# Verbose mode (shows Farkas witnesses)
zkverify --verbose program.gc
```

### Verification Obligations

The tool checks four types of obligations:

1. **Initial Condition**: A_0 x ≤ b_0 ⟹ V(x,q) well-defined and > 0
   - Ensures initial states have positive ranking values

2. **Well-Definedness**: T(x,x') ∧ σ(x) ∧ V(x,q) defined ⟹ V(x',q') well-defined and > 0
   - Ensures transitions preserve ranking function well-definedness

3. **Non-Increasing**: T(x,x') ∧ σ(x) ∧ V(x,q) defined ⟹ V(x,q) ≥ V(x',q')
   - Ensures ranking doesn't increase on any transition

4. **Strictly Decreasing** (fair transitions only): T(x,x') ∧ σ(x) ∧ V(x,q) defined ⟹ V(x,q) > V(x',q')
   - Ensures ranking strictly decreases on fair/accepting transitions

### Example

Input file `counter.gc`:
```
const maxVal = 10

init: x = 0

[] x < maxVal -> x = x + 1

rank(q0):
  [] x >= 0 && x < maxVal + 1 -> maxVal + 1 - x

trans(q0, q0): x < maxVal
```

Verification:
```bash
$ zkverify counter.gc
3/3 obligations verified
```

With verbose output:
```bash
$ zkverify --verbose counter.gc
Verification Results for counter.gc
============================================================

[1/3] ✓ PASS: initial
     Ranking state: q0
     Witness: {'lambda_s_0': 1, 'lambda_s_1': 0, ...}

[2/3] ✓ PASS: well_defined
     Program transition: 0
     Automaton transition: q0 → q0
     Ranking state: q0

[3/3] ✓ PASS: non_increasing
     Program transition: 0
     Automaton transition: q0 → q0
     Ranking state: q0

============================================================
3/3 obligations verified
```

### Python API (Verification)

```python
from zkterm_tool import parse_with_constants, verify_termination

# Parse and verify
result = parse_with_constants(text)
verification = verify_termination(result)

# Check results
if verification.passed:
    print(verification.summary())  # "5/5 obligations verified"
else:
    for obl in verification.failed_obligations():
        print(f"Failed: {obl}")

# Get Farkas witnesses (proof certificates)
witnesses = verification.get_witnesses()
for w in witnesses:
    print(f"Witness: {w}")  # {'lambda_s_0': 1, 'mu_p_0': 2, ...}
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

### Initial Conditions

```
init: guard
```

- **Guard**: Conjunction of comparisons specifying initial state constraints
- Encodes to $A_0 x \leq b_0$ where $x$ contains current-state variables only

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

### Büchi Automaton Transitions

```
trans(from_state, to_state): guard       // Regular transition (δ only)
trans!(from_state, to_state): guard      // Fair transition (δ and F)
```

- **States**: Identifiers for automaton states (e.g., `q0`, `q1`)
- **Guard**: Conjunction of comparisons on current-state variables
- **Fair marker** (`!`): Marks transition as fair/accepting (included in F set)
- Regular transitions encode to δ only, fair transitions encode to both δ and F

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

### Initial Condition Encoding

Initial conditions are encoded as $A_0 x \leq b_0$ where:

1. **Guards** become constraints on current-state variables (no primed variables)
2. Each comparison is converted to one or more linear inequalities
3. Equality `x = c` becomes two inequalities: `x ≤ c` and `x ≥ c`
4. Strict inequalities `x < c` are converted to non-strict: `x ≤ c-1` (integer semantics)

### Transition Encoding (zkterm)

The encoding uses integer semantics with all inequalities converted to non-strict form:

1. **Guards** become constraints on current-state variables
2. **Assignments** `var' = expr` become equality constraints (encoded as two inequalities)
3. **Unassigned variables** get identity constraints: `var' = var`
4. **Strict inequalities** (`<`, `>`) are automatically converted to non-strict using integer semantics: `x < c` → `x ≤ c-1`

All constraints are encoded in the $(A, b)$ matrix pair where $Ax \leq b$.

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

### Büchi Automaton Transition Encoding

Each automaton transition is encoded as:

1. **δ transitions** (all): $A^{(q,q')}_\delta x \leq b^{(q,q')}_\delta$
   - Guard constraints on current-state variables only
   - All transitions (regular and fair) are included

2. **F transitions** (fair only): $A^{(q,q')}_F x \leq b^{(q,q')}_F$
   - Only transitions marked with `!` are included
   - For fair transitions: $A_F = A_\delta$ and $b_F = b_\delta$ (same constraints)

Variables in automaton transitions are current-state only (no primed variables).

## Development

```bash
# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=zkterm_tool
```

## License

MIT
