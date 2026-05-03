# zkmc — zero-knowledge model checking

Monorepo for the zkmc toolchain: proving termination of guarded-command programs and exporting the obligations as zero-knowledge proofs.

## Layout

- [`encoder/`](encoder/) — Python toolkit (`zkterm`, `zkrank`, `zkverify`, `zkfarkas`, `zkexplicit`). Encodes guarded commands and ranking functions into matrix/vector form, discharges termination obligations via Farkas' lemma + Z3, and exports JSON consumable by the prover. See [`encoder/README.md`](encoder/README.md) and [`encoder/LANGUAGE.md`](encoder/LANGUAGE.md).
- [`zkmc-explicit/`](zkmc-explicit/) — Rust implementation of explicit-case ZKP specified in ZKMC paper. Takes JSON as input, benchmarks time to setup, prove, and verify - see [`zkmc-explicit/README.md`](zkmc-explicit/README.md) for installation and usage instructions.
- [`zkmc-symbolic/`](zkmc-symbolic/) — Rust implementation of symbolic-case ZKP specified in ZKMC paper. Takes JSON as input, benchmarks time to setup, prove, and verify - see [`zkmc-symbolic/README.md`](zkmc-symbolic/README.md) for installation and usage instructions.  

## Status

Academic implementation, not production ready.

## License

MIT
