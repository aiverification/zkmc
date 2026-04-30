# zkmc — zero-knowledge model checking

Monorepo for the zkmc toolchain: proving termination of guarded-command programs and exporting the obligations as zero-knowledge proofs.

## Layout

- [`encoder/`](encoder/) — Python toolkit (`zkterm`, `zkrank`, `zkverify`, `zkfarkas`, `zkexplicit`). Encodes guarded commands and ranking functions into matrix/vector form, discharges termination obligations via Farkas' lemma + Z3, and exports JSON consumable by the prover. See [`encoder/README.md`](encoder/README.md) and [`encoder/LANGUAGE.md`](encoder/LANGUAGE.md).
- _`zkmc-explicit/` — Rust ZK proof system. Coming soon._
- _`zkmc-symbolic/` — Rust ZK proof system. Coming soon._

## Status

Academic implementation, not production ready.

## License

MIT
