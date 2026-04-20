# zkterm-tool

A Python toolkit that turns guarded-command programs, their Büchi automata, and ranking functions into matrix/vector form for formal termination verification — and exports the resulting obligations in shapes that downstream zero-knowledge proof systems can consume.

The toolkit covers four concerns:

1. **Program encoding** — guarded commands → matrix inequalities `A x ≤ b`.
2. **Ranking encoding** — piecewise-linear ranking functions → `(W_j, u_j, C_j, d_j)` per case.
3. **Symbolic verification** — check termination obligations via Farkas' lemma and Z3.
4. **ZK-friendly export** — Farkas duals as JSON, or explicit-state enumeration with field embeddings.

The `.gc` input language is described in [LANGUAGE.md](LANGUAGE.md).

## Installation

```bash
git clone <repo-url>
cd zkterm-tool
uv sync
```

## Quick start

Save a small program as `counter.gc`:

```
const maxVal = 10
type x: 0..maxVal

init: x = 0

[] x < maxVal -> x = x + 1

rank(q0):
  [] x >= 0 && x <= maxVal -> maxVal - x
  [] x < 0 -> inf
  [] x > maxVal -> inf

automaton_init: q0
trans(q0, q0): x < maxVal
```

Verify that it terminates:

```bash
uv run zkverify counter.gc
# 5/5 obligations verified
```

The `inf` cases mark regions of the state space on which the ranking is deliberately undefined; see [LANGUAGE.md](LANGUAGE.md) for the full language.

For the full `.gc` language — constants, types, initial conditions, guarded commands, ranking functions, and Büchi automata — see [LANGUAGE.md](LANGUAGE.md).

## Command-line tools

The package installs five commands. All accept `.gc` input and share the `--const NAME=VALUE` flag for overriding constants.

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `zkterm` | Encode guarded commands, init, and automaton transitions as matrix inequalities `A x ≤ b` | `.gc` file or stdin | Matrices (optionally symbolic with `-s`) |
| `zkrank` | Encode ranking functions as `(W_j, u_j, C_j, d_j)` per case | `.gc` file or stdin | Ranking-function encodings |
| `zkverify` | Verify termination obligations via Farkas' lemma + Z3 | `.gc` file | Pass/fail summary with witnesses (`-v`) |
| `zkfarkas` | Export Farkas dual obligations as JSON for external solvers / ZK pipelines | `.gc` file | JSON: `A_s`, `b_s`, `G_p`, `h_p`, multipliers |
| `zkexplicit` | Explicit-state verification by enumeration, plus BN254 field embeddings | `.gc` file + bounds | JSON: violation/valid sets, embeddings |

Each tool has complete flag documentation via `--help`; what follows are one-line intros and minimal invocations.

### `zkterm` — encode programs

Turns guards, assignments, init conditions, and automaton transitions into the matrix form consumed by the verifier and by downstream ZK tooling.

```bash
echo '[] y < z -> y = y + 1' | uv run zkterm
uv run zkterm -s program.gc      # symbolic, variable-named output
```

Run `uv run zkterm --help` for all flags.

### `zkrank` — encode ranking functions

Emits, for each case of each ranking function, the guard matrix `C_j`, guard vector `d_j`, coefficient vector `W_j`, and constant `u_j`. Useful as a stand-alone inspection tool when authoring a `.gc` file.

```bash
echo 'rank(q0): [] x > 0 -> x' | uv run zkrank
uv run zkrank -s program.gc      # symbolic output
```

Run `uv run zkrank --help` for all flags.

### `zkverify` — verify termination

Discharges the full set of termination obligations using a disjunctive Farkas formulation and Z3. Returns pass/fail with optional Farkas witnesses.

```bash
uv run zkverify program.gc
uv run zkverify --verbose program.gc    # show Farkas witnesses per obligation
```

Three obligation kinds are discharged:

- **Initial non-infinity** — initial states do not fall in an infinity region of the ranking.
- **Transition non-infinity** — program transitions out of a finite ranking region stay out of infinity in the target state.
- **Update** — the ranking decreases (strictly on fair transitions, non-increasing on regular ones).

Run `uv run zkverify --help` for all flags.

### `zkfarkas` — export Farkas duals as JSON

Produces the same obligations that `zkverify` discharges, but emitted as JSON Farkas duals plus witnesses. The format is intended to be consumed by external SMT/LP solvers or wired into a zero-knowledge proof system.

```bash
uv run zkfarkas --pretty program.gc > obligations.json
```

Each obligation entry carries matrices `A_s`, `b_s`, `G_p`, `h_p`, multipliers `λ_s`, `μ_s`, and a few pre-computed convenience vectors and scalars.

Run `uv run zkfarkas --help` for all flags.

### `zkexplicit` — explicit-state verification + embeddings

Enumerates the concrete state space within user-specified (or type-declared) bounds, computes the violation sets `B_init`, `B_step`, `B_fairstep` and the valid sets `S`, `S_0`, `T`, verifies disjointness, and embeds everything into a prime field suitable for polynomial commitments (KZG-style). Defaults to the BLS12-381 scalar field.

```bash
uv run zkexplicit program.gc --bounds x:0:10
uv run zkexplicit program.gc --pretty       # uses type-declared bounds
```

Run `uv run zkexplicit --help` for all flags.

## Examples and benchmarks

Sample `.gc` programs live in [`examples/`](examples/) (see [`examples/README.md`](examples/README.md) for a tour). Performance-benchmark harnesses live in [`benchmarks/`](benchmarks/) — see [`benchmarks/README.md`](benchmarks/README.md).

## Development

```bash
uv run pytest
uv run pytest --cov=zkterm_tool
```

## License

MIT
