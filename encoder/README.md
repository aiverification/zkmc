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

Define ranking functions for automaton states with explicit finite and infinity cases:

```
rank(q0):
  [] x >= 0 && x < 10 -> 10 - x
  [] x >= 10 && x < 20 -> 1
  [] x < 0 -> inf
  [] x >= 20 -> inf
```

Each case has:
- **Guard**: Conjunction of linear inequalities (like guarded commands)
- **Expression**: Linear expression computing the ranking value, or `inf` for infinity cases

**Finite cases** have linear expressions (e.g., `10 - x`, `1`).
**Infinity cases** use the keyword `inf` to mark states where the ranking is undefined (+∞).

**Semantics**: Cases are checked in order (first-match). All states must be covered by at least one case (finite or infinity).

### Example

Input:
```
const maxVal = 10

rank(q0):
  [] x >= 0 && x < maxVal -> maxVal - x
  [] x >= maxVal && x < 20 -> 1
  [] x < 0 -> inf
  [] x >= 20 -> inf
```

Matrix output (`zkrank program.gc`):
```
=== Ranking Function for State q0 ===
Variables: [x]

Case 1:
  Guard C_j x <= d_j:
    C_j =
      [ -1]
      [  1]
    d_j = [  0  10]
  Expression W_j x + u_j:
    W_j = [ -1]
    u_j = 10

Case 2:
  Guard C_j x <= d_j:
    C_j =
      [ -1]
    d_j = [-10]
  Expression W_j x + u_j:
    W_j = [  0]
    u_j = 1
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
  Guard P x <= r:
    P =
      [ -1]
      [  1]
    r = [  0   4]
  Fair: NO

Transition: q1 -> q1 (FAIR)
  Guard P x <= r:
    P =
      [ -1]
      [  1]
    r = [ -1   9]
  Fair: YES

Transition: q1 -> q0
  Guard P x <= r:
    P =
      [ -1]
    r = [-10]
  Fair: NO
```

Symbolic output (`zkterm -s program.gc`):
```
=== Automaton Transitions ===
Variables: [x]

Transition: q0 -> q1
  Guard: -x <= 0 && x <= 4
  P x <= r:
    -x <= 0
    x <= 4
  Fair: NO

Transition: q1 -> q1 (FAIR)
  Guard: -x <= -1 && x <= 9
  P x <= r:
    -x <= -1
    x <= 9
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
        # Guard: C_j x <= d_j
        print(f"  Case {i+1} guard: C_j={case.C_j}, d_j={case.d_j}")
        # Expression: W_j x + u_j
        print(f"           expr: W_j={case.W_j}, u_j={case.u_j}")
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
    print(f"Guard: P = {enc.P}, r = {enc.r}")
    print(f"Fair: {enc.is_fair}")
```

## Verification (zkverify)

The `zkverify` tool verifies termination obligations for guarded command programs using Farkas lemma and Z3 SMT solver.

### Command Line

```bash
# Verify a program
zkverify program.gc

# Verbose mode (shows Farkas witnesses)
zkverify --verbose program.gc

# Skip validation checks (disjointness, coverage, non-negativity)
zkverify --skip-validation program.gc
```

### Ranking Function Validation

By default, `zkverify` validates ranking functions before verification:

1. **Disjointness**: All case guards must be pairwise disjoint
2. **Complete coverage**: Cases must cover the entire state space
3. **Non-negativity**: Finite cases must be non-negative under their guards

Use `--skip-validation` to bypass these checks if needed.

### Verification Obligations

The tool checks three types of obligations:

1. **Initial Non-Infinity**: A_0 x ≤ b_0 ⟹ E_k x > f_k
   - For each infinity case k, ensures initial states don't satisfy the infinity guard
   - Prevents the ranking from being undefined at initial states

2. **Transition Non-Infinity**: A_i [x;x'] ≤ b_i ⟹ [P; C_j] x ≤ [r; d_j] ⟹ E_k x > f_k
   - For each finite case j and infinity case k, ensures transitions from finite regions don't reach infinity
   - Prevents the ranking from becoming undefined during execution

3. **Update** (Ranking Decrease): A_i [x;x'] ≤ b_i ⟹ [P; C_j; C_k'] [x;x'] ≤ [r; d_j; d_k] ⟹ [w_j, -w_k] [x;x'] > u_k - u_j + ζ
   - For each finite source case j and target case k, ensures ranking decreases by at least ζ
   - ζ=1 for fair transitions (strict decrease required), ζ=0 for regular transitions (non-increasing)

**Obligation count**: For p program transitions, a automaton transitions, s states, m_j finite source cases, m_k finite target cases, l infinity cases:
- Initial non-infinity: s × l (one per state × infinity cases)
- Transition non-infinity: p × a × m_j × l (one per program transition × automaton transition × finite source cases × infinity cases)
- Update: p × a × m_j × m_k (one per program transition × automaton transition × source cases × target cases)

**Requirements**:
- Programs must include automaton transitions for verification
- Ranking functions must include explicit infinity cases (use `[] guard -> inf` syntax)
- All states must be covered by finite or infinity cases (validated automatically unless `--skip-validation` is used)

### Example

Input file `counter.gc`:
```
const maxVal = 10

init: x = 0

[] x < maxVal -> x = x + 1

rank(q0):
  [] x >= 0 && x < maxVal + 1 -> maxVal + 1 - x
  [] x < 0 -> inf
  [] x >= maxVal + 1 -> inf

trans(q0, q0): x < maxVal
```

Verification:
```bash
$ zkverify counter.gc
5/5 obligations verified
```

With verbose output:
```bash
$ zkverify --verbose counter.gc
Verification Results for counter.gc
============================================================

[1/5] ✓ PASS: initial_non_infinity
     Source state: q0
     Infinity case: 0
     Witness: {'lambda_s_0': 0, 'lambda_s_1': 1, 'mu_s_0': 1}

[2/5] ✓ PASS: initial_non_infinity
     Source state: q0
     Infinity case: 1
     Witness: {'lambda_s_0': 0, 'lambda_s_1': 1, 'mu_s_0': 1}

[3/5] ✓ PASS: transition_non_infinity
     Program transition: 0
     Automaton transition: q0 → q0
     Source state: q0 [case 0]
     Infinity case: 0
     Witness: {...}

[4/5] ✓ PASS: transition_non_infinity
     Program transition: 0
     Automaton transition: q0 → q0
     Source state: q0 [case 0]
     Infinity case: 1
     Witness: {...}

[5/5] ✓ PASS: update
     Program transition: 0
     Automaton transition: q0 → q0
     Source state: q0 [case 0]
     Target state: q0 [case 0]
     Witness: {...}

============================================================
5/5 obligations verified
```

### Farkas Dual Formulations (zkfarkas)

The `zkfarkas` tool outputs Farkas dual formulations as JSON, including witness values computed by Z3 for each obligation. This provides all data needed for zero-knowledge proofs and external verification.

#### Command Line

```bash
# Output JSON to stdout
zkfarkas program.gc

# Pretty-printed JSON
zkfarkas --pretty program.gc

# Save to file
zkfarkas program.gc > obligations.json
```

#### Output Format

Each obligation is encoded in the disjunctive Farkas formulation:

```
∀y: A_s y ≤ b_s ⟹ C y ≤ d ⟹ ∨_k E_k y > f_k
```

The tool uses Z3 to find witness values (`lambda_s`, `mu_s`) and computes a convenience value (`neg_b_s_T_lambda_s = -b_s^T λ_s`) for ZK proof systems.

Example output:
```json
{
  "obligations": [
    {
      "obligation_type": "update",
      "matrices": {
        "A_s": [[1, 0], [-1, 1], [1, -1]],
        "b_s": [4, 1, -1],
        "G_p": [[-1, 1]],
        "h_p": [-1]
      },
      "dimensions": {
        "n_vars": 2,
        "n_lambda_s": 3,
        "n_mu_s": 1
      },
      "program_transition": 0,
      "automaton_transition": {
        "from": "q0",
        "to": "q0"
      },
      "source_ranking_state": "q0",
      "target_ranking_state": "q0",
      "source_case_idx": 0,
      "target_case_idx": 0,
      "witness": {
        "lambda_s": [1, 0, 2],
        "mu_s": [3]
      },
      "computed_values": {
        "neg_b_s_T_lambda_s": -2
      },
      "satisfiable": true
    }
  ],
  "count": 1
}
```

Each obligation includes:
- **matrices**: Uniform pattern (A_s, b_s secret; G_p, h_p public)
- **dimensions**: Sizes (n_vars, n_lambda_s, n_mu_s)
- **witness**: Z3-computed Farkas multipliers (lambda_s, mu_s) if satisfiable
- **computed_values**: Convenience value (neg_b_s_T_lambda_s) for ZK proof systems
- **metadata**: obligation_type, source/target states, source_case_idx, target_case_idx, is_fair, transitions
- **satisfiable**: Whether Z3 found a witness for this obligation

#### Python API (Farkas JSON)

```python
from zkterm_tool import extract_farkas_obligations
import numpy as np

# Extract all obligations with Z3-computed witnesses
obligations = extract_farkas_obligations("program.gc")

for obl in obligations:
    print(f"{obl['obligation_type']}: satisfiable={obl['satisfiable']}")

    # Access matrices (uniform pattern: A_s y ≤ b_s ⟹ G_p y ≰ h_p)
    matrices = obl["matrices"]
    A_s = np.array(matrices["A_s"])  # Secret premise matrix
    b_s = np.array(matrices["b_s"])  # Secret premise vector
    G_p = np.array(matrices["G_p"])  # Public constraint matrix
    h_p = np.array(matrices["h_p"])  # Public constraint vector

    # Access witness values (computed by Z3)
    if obl["witness"] is not None:
        lambda_s = obl["witness"]["lambda_s"]  # Secret multipliers
        mu_s = obl["witness"]["mu_s"]          # Public multipliers

        # Access convenience value for ZK proofs
        neg_b_s_T_lambda_s = obl["computed_values"]["neg_b_s_T_lambda_s"]

        print(f"  Witness found: lambda_s={lambda_s}, mu_s={mu_s}")
        print(f"  Convenience: -b_s^T lambda_s = {neg_b_s_T_lambda_s}")
```

### Python API (Verification)

```python
from zkterm_tool import parse_with_constants, verify_termination

# Parse and verify
result = parse_with_constants(text)
verification = verify_termination(result)

# Check results
if verification.passed:
    print(verification.summary())  # e.g., "2/2 obligations verified"
else:
    for obl in verification.failed_obligations():
        print(f"Failed: {obl}")

# Get Farkas witnesses (proof certificates)
witnesses = verification.get_witnesses()
for w in witnesses:
    print(f"Witness: {w}")  # {'lambda_s_0': 1, 'mu_s_0': 2, ...}
```

## Explicit-State Verification (zkexplicit)

The `zkexplicit` tool performs explicit-state verification by enumerating concrete states and identifying which ones violate termination obligations. This supports zero-knowledge proof systems based on polynomial commitments (KZG) by computing violation sets that must be proven disjoint from the secret initial states and transition relation.

### Command Line

```bash
# Basic usage
zkexplicit program.gc --bounds x:0:10

# Multiple variables
zkexplicit program.gc --bounds x:0:10 y:0:5

# Pretty-printed JSON
zkexplicit program.gc --bounds x:0:10 --pretty

# With field embeddings for polynomial commitments
zkexplicit program.gc --bounds x:0:10 --embeddings

# Custom field size
zkexplicit program.gc --bounds x:0:10 --embeddings --field-size 101
```

### Violation Sets and Valid Sets

The tool computes both violation sets (bad sets) and valid sets needed for ZK proof construction:

**Violation Sets** (by contraposition):
1. **B_init**: States where V(s,q) = ∞ for some initial automaton state q ∈ Q_0
   - These are states where the ranking function is undefined

2. **B_step**: Transitions (s,s') where V(s,q) < V(s',q') for enabled transitions
   - These are transitions where the ranking increases (violates non-increasing requirement)

3. **B_fairstep**: Fair transitions (s,s') where V(s,q) ≤ V(s',q')
   - These are fair transitions where ranking doesn't strictly decrease

**Valid Sets** (for polynomial construction):
1. **S**: Complete state space (all enumerated states within bounds)
2. **S0**: Initial states (states satisfying the init condition)
3. **T**: Program transition relation (valid transitions according to program semantics)

**Zero-knowledge proof goal**: Prove that S_0 ∩ B_init = ∅, T ∩ B_step = ∅, and T ∩ B_fairstep = ∅.

The tool automatically verifies these disjointness properties and includes the results in the output.

### Options

| Option | Description |
|--------|-------------|
| `--bounds VAR:MIN:MAX` | Required. State space bounds for each variable (e.g., `x:0:10 y:0:5`) |
| `--pretty` | Pretty-print JSON output with indentation |
| `--embeddings` | Include field embeddings e_1 and e_2 for polynomial commitments |
| `--field-size N` | Prime field size for embeddings (default: 2^256-189, BN254 scalar field) |

### Example

Input file `counter.gc`:
```
const maxVal = 10

init: x = 0

[] x < maxVal -> x = x + 1

rank(q0):
  [] x >= 0 && x <= maxVal -> maxVal - x

trans(q0, q0): x < maxVal
```

Run explicit-state verification:
```bash
$ zkexplicit counter.gc --bounds x:0:15 --pretty
{
  "B_init": [{"x": 11}, {"x": 12}, {"x": 13}, {"x": 14}, {"x": 15}],
  "B_step": [{"from": {"x": 0}, "to": {"x": 11}}, ...],
  "B_fairstep": [],
  "S": [{"x": 0}, {"x": 1}, ..., {"x": 15}],
  "S0": [{"x": 0}],
  "T": [{"from": {"x": 0}, "to": {"x": 1}}, ..., {"from": {"x": 9}, "to": {"x": 10}}],
  "verification": {
    "init_disjoint": true,
    "step_disjoint": false,
    "fairstep_disjoint": true,
    "all_disjoint": false,
    "init_intersection_size": 0,
    "step_intersection_size": 95,
    "fairstep_intersection_size": 0
  },
  "metadata": {
    "variables": ["x"],
    "automaton_states": ["q0"],
    "num_states_enumerated": 16,
    "num_transitions_checked": 256,
    "set_sizes": {
      "S": 16,
      "S0": 1,
      "T": 10,
      "B_init": 5,
      "B_step": 95,
      "B_fairstep": 0
    }
  }
}
```

With embeddings:
```bash
$ zkexplicit counter.gc --bounds x:8:12 --embeddings --field-size 101 --pretty
{
  "B_init": [{"x": 11}, {"x": 12}],
  "B_step": [...],
  "B_fairstep": [],
  "metadata": {...},
  "embeddings": {
    "E_init": [11, 12],
    "E_step": [8, 8, 9, 9, 9],
    "E_fairstep": [],
    "field_size": 101
  }
}
```

### Field Embeddings

When `--embeddings` is specified, the tool computes injective mappings for polynomial commitment schemes:

- **e_1: S → F** (state embedding): Maps states to field elements
  - Formula: e_1([v_1, v_2, ..., v_n]) = ∑_i v_i * base^i mod field_size

- **e_2: S × S → F** (transition embedding): Maps transitions to field elements
  - Formula: e_2([s, s']) = e_1(s) + e_1(s') * field_size

These embeddings support KZG polynomial commitments for zero-knowledge proofs of set disjointness.

### Python API (Explicit-State)

```python
from zkterm_tool import (
    parse_with_constants,
    encode_program,
    encode_ranking_functions,
    encode_automaton_transitions,
    encode_init,
    create_state_space,
    compute_violation_sets,
    compute_embeddings,
    verify_disjointness,
    violations_to_json
)

# Parse program
result = parse_with_constants(text)

# Encode components
rank_encs = encode_ranking_functions(result.ranking_functions)
aut_encs = encode_automaton_transitions(result.automaton_transitions)
trans_encs = encode_program(result.commands, nonstrict_only=True)
init_enc = encode_init(result.init_condition) if result.init_condition else None

# Create state space from bounds
variables = sorted(set().union(
    *[set(enc.variables) for enc in rank_encs.values()],
    *[set(enc.variables) for enc in aut_encs],
    *[set(enc.variables) for enc in trans_encs]
))
state_space = create_state_space(variables, ["x:0:10", "y:0:5"])

# Compute violation sets and valid sets
violations = compute_violation_sets(
    state_space,
    rank_encs,
    aut_encs,
    init_enc,
    list(rank_encs.keys()),  # Initial automaton states
    trans_encs
)

# Access violation sets
print(f"B_init: {len(violations.B_init)} states")
print(f"B_step: {len(violations.B_step)} transitions")
print(f"B_fairstep: {len(violations.B_fairstep)} transitions")

# Access valid sets
print(f"S: {len(violations.S)} states")
print(f"S0: {len(violations.S0)} initial states")
print(f"T: {len(violations.T)} transitions")

# Verify disjointness
verification = verify_disjointness(violations)
print(f"All disjoint: {verification.all_disjoint}")
print(f"S0 ∩ B_init = ∅: {verification.init_disjoint}")
print(f"T ∩ B_step = ∅: {verification.step_disjoint}")
print(f"T ∩ B_fairstep = ∅: {verification.fairstep_disjoint}")

# Optionally compute embeddings
embeddings = compute_embeddings(violations, field_size=101)
print(f"Field size: {embeddings.field_size}")

# Convert to JSON
output = violations_to_json(violations, embeddings, verification)
```

### Performance Considerations

**State space size**: O(∏_i (max_i - min_i + 1))
**Transition checks**: O(n_states² × n_automaton_transitions)

For large state spaces, the tool may be slow. Consider restricting bounds to smaller ranges or using symbolic verification (zkverify) instead.

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
  [] guard -> expression     // Finite case
  [] guard -> expression     // Finite case
  [] guard -> inf            // Infinity case
  ...
```

- **State name**: Identifier for automaton state (e.g., `q0`, `q1`)
- **Guard**: Conjunction of comparisons (same as guarded commands)
- **Expression**: Linear arithmetic expression (for finite cases) or `inf` (for infinity cases)
- **Semantics**: First-match (cases checked in order)
- **Coverage**: All states must be covered by at least one case (finite or infinity)

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

Ranking functions have two types of cases:

**Finite cases** (j = 1...m):

1. **Guard** `C_j x ≤ d_j` encodes the condition for this case
   - Comparisons are converted to inequalities
   - Multiple comparisons in conjunction become multiple rows in C_j

2. **Expression** `w_j x + u_j` encodes the ranking value
   - w_j is a row vector of variable coefficients
   - u_j is the constant term
   - For expression `2x + 3y - 1`: w_j = [2, 3], u_j = -1

**Infinity cases** (k = 1...l):

1. **Guard** `E_k x ≤ f_k` encodes when the ranking is +∞
   - Uses the keyword `inf` instead of an expression
   - Encodes only the guard (no expression coefficients)

**Notation**: Finite cases use (C_j, d_j, w_j, u_j), infinity cases use (E_k, f_k).

Cases are ordered (first satisfied guard determines the value at runtime). All states must be covered by at least one case (finite or infinity) - this is validated automatically unless `--skip-validation` is used.

### Büchi Automaton Transition Encoding

Each automaton transition is encoded as:

- **Guard**: $P^{(q,q')} x \leq r^{(q,q')}$
  - Guard constraints on current-state variables only
  - All transitions (regular and fair) have guard encoding

- **Fair flag**: `is_fair` boolean
  - Regular transitions (`trans`): `is_fair = False`
  - Fair transitions (`trans!`): `is_fair = True`
  - This flag affects verification (ζ parameter): fair requires strict decrease, regular allows non-increasing

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
