# zkmc — zero-knowledge model checking

Monorepo for the zkmc toolchain: proving termination of guarded-command programs and exporting the obligations as zero-knowledge proofs.

## Layout

- [`encoder/`](encoder/) — Python toolkit (`zkterm`, `zkrank`, `zkverify`, `zkfarkas`, `zkexplicit`, `zkltl`, `zksynth`, `zkits`). Encodes guarded commands and ranking functions into matrix/vector form, discharges termination obligations via Farkas' lemma + Z3, and exports JSON consumable by the prover. Programs can be written as `.gc` guarded commands or imported from KoAT `.koat` integer transition systems (`zkits`); properties written directly as a Büchi automaton or in LTL (`spec:`, via [Spot](https://spot.lre.epita.fr/)); ranking functions written by hand or synthesized automatically (`zksynth` / `zkverify --synthesize`). See [`encoder/README.md`](encoder/README.md) and [`encoder/LANGUAGE.md`](encoder/LANGUAGE.md).
- [`zkmc-explicit/`](zkmc-explicit/) — Rust implementation of explicit-case ZKP specified in ZKMC paper. Takes JSON as input, benchmarks time to setup, prove, and verify - see [`zkmc-explicit/README.md`](zkmc-explicit/README.md) for installation and usage instructions.
- [`zkmc-symbolic/non-folding`](zkmc-symbolic/non-folding) — Rust implementation of the non-folding symbolic-case ZKP specified in ZKMC paper. Takes JSON as input, benchmarks time to setup, prove, and verify - see [`zkmc-symbolic/non-folding/README.md`](zkmc-symbolic/non-folding/README.md) for installation and usage instructions.  
- [`zkmc-symbolic/folding`](zkmc-symbolic/folding) — Rust implementation of the folding symbolic-case ZKP specified in ZKMC paper. Takes JSON as input, benchmarks time to setup, prove, and verify - see [`zkmc-symbolic/folding/README.md`](zkmc-symbolic/folding/README.md) for installation and usage instructions.  

## Status

Academic implementation, not production ready.

## License

MIT
