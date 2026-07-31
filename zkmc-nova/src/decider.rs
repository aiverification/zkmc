//! Produces and independently verifies the final offchain Nova proof.

use crate::{
    artifact::{read_compressed, read_json, write_compressed, write_json},
    circuit::ZkmcCircuit,
    commitment::{certificate_seed, model_seed},
    metrics::{print_duration, print_u64},
    model::Batch,
    statement::{load_statement, CommitmentStatement, BUNDLED_STATEMENT},
    AppResult,
};
use ark_groth16::Groth16;
use ark_mnt4_298::{Fr, G1Projective as G1, MNT4_298 as MNT4};
use ark_mnt6_298::{Fr as Fr2, G1Projective as G2, MNT6_298 as MNT6};
use ark_serialize::{CanonicalDeserialize, CanonicalSerialize};
use ark_snark::SNARK;
use folding_schemes::{
    commitment::{kzg::KZG, CommitmentScheme},
    folding::{
        nova::{
            decider::{
                Decider as OffchainDecider, Proof as DeciderProof,
                VerifierParam as DeciderVerifierParam,
            },
            Nova,
        },
        traits::CommittedInstanceOps,
    },
    frontend::FCircuit,
    Decider, FoldingScheme,
};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{self, BufWriter, Write},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub type NovaScheme = Nova<G1, G2, ZkmcCircuit<Fr>, KZG<'static, MNT4>, KZG<'static, MNT6>, false>;

pub type FinalDecider = OffchainDecider<
    G1,
    G2,
    ZkmcCircuit<Fr>,
    KZG<'static, MNT4>,
    KZG<'static, MNT6>,
    Groth16<MNT4>,
    Groth16<MNT6>,
    NovaScheme,
>;

pub type NovaParams = (
    <NovaScheme as FoldingScheme<G1, G2, ZkmcCircuit<Fr>>>::ProverParam,
    <NovaScheme as FoldingScheme<G1, G2, ZkmcCircuit<Fr>>>::VerifierParam,
);

type StoredProof =
    DeciderProof<G1, G2, KZG<'static, MNT4>, KZG<'static, MNT6>, Groth16<MNT4>, Groth16<MNT6>>;

type StoredVerifier = DeciderVerifierParam<
    G1,
    <KZG<'static, MNT4> as CommitmentScheme<G1>>::VerifierParams,
    <Groth16<MNT4> as SNARK<Fr>>::VerifyingKey,
    <KZG<'static, MNT6> as CommitmentScheme<G2>>::VerifierParams,
    <Groth16<MNT6> as SNARK<Fr2>>::VerifyingKey,
>;

pub const PROOF_FILE: &str = "decider_proof.bin";
pub const VERIFIER_FILE: &str = "decider_verifier.bin";
pub const PUBLIC_FILE: &str = "decider_public.bin";
pub const MANIFEST_FILE: &str = "manifest.json";
const DECIDER_NAME: &str = "Sonobe Nova offchain decider";
const CURVE_CYCLE: &str = "MNT4-298/MNT6-298";
const SONOBE_REVISION: &str = "9b7dd34f0e0341046baeabc6f900f5ee63007f18";
const ZKMC_UPSTREAM_COMMIT: &str = "112b470337cbe13c8b1aa21dc9bd199eb6ce5a40";

#[derive(Debug, Deserialize, Serialize)]
struct Manifest {
    schema_version: u32,
    benchmark: String,
    obligation_count: usize,
    bound: u64,
    decider: String,
    curve_cycle: String,
    model_blinding_commitment: String,
    model_commitment: String,
    certificate_commitment: String,
    initial_state: Vec<String>,
    final_state: Vec<String>,
    sonobe_revision: String,
    zkmc_upstream_commit: String,
    files: ArtifactFiles,
}

#[derive(Debug, Deserialize, Serialize)]
struct ArtifactFiles {
    statement: FileEntry,
    proof: FileEntry,
    verifier: FileEntry,
    public_inputs: FileEntry,
}

#[derive(Debug, Deserialize, Serialize)]
struct FileEntry {
    name: String,
    bytes: u64,
}

struct PublicPacket {
    i: Fr,
    z_0: Vec<Fr>,
    z_i: Vec<Fr>,
    running_commitments: Vec<G1>,
    incoming_commitments: Vec<G1>,
}

/// Generates, verifies, and stores the final proof.
pub fn finalize_decider(
    batch: &Batch,
    statement: &CommitmentStatement,
    nova_params: NovaParams,
    circuit: ZkmcCircuit<Fr>,
    nova: NovaScheme,
    output_dir: impl AsRef<Path>,
) -> AppResult<()> {
    let output_dir = output_dir.as_ref();
    fs::create_dir_all(output_dir)?;
    let mut rng = ark_std::rand::rngs::OsRng;

    let setup_start = Instant::now();
    let (decider_pp, decider_vp) =
        FinalDecider::preprocess(&mut rng, (nova_params, circuit.state_len()))?;
    let setup_elapsed = setup_start.elapsed();
    println!("offchain decider setup completed in {setup_elapsed:?}");
    print_duration("decider_setup_seconds", setup_elapsed);

    let prove_start = Instant::now();
    let proof = FinalDecider::prove(&mut rng, decider_pp, nova.clone())?;
    let prove_elapsed = prove_start.elapsed();
    println!("offchain decider proof generated in {prove_elapsed:?}");
    print_duration("decider_prove_seconds", prove_elapsed);

    let verify_start = Instant::now();
    verify_live_decider(&decider_vp, &proof, &nova)?;
    let verify_elapsed = verify_start.elapsed();
    print_duration("in_memory_verify_seconds", verify_elapsed);
    println!("in-memory offchain decider verification passed");

    let statement_path = output_dir.join(BUNDLED_STATEMENT);
    let proof_path = output_dir.join(PROOF_FILE);
    let verifier_path = output_dir.join(VERIFIER_FILE);
    let public_path = output_dir.join(PUBLIC_FILE);
    let manifest_path = output_dir.join(MANIFEST_FILE);

    let serialization_start = Instant::now();
    write_json(&statement_path, statement)?;
    let statement_bytes = fs::metadata(&statement_path)?.len();
    let proof_bytes = write_compressed(&proof_path, &proof)?;
    let verifier_bytes = write_compressed(&verifier_path, &decider_vp)?;
    let public_bytes = write_public_packet(&public_path, &nova)?;
    print_duration("serialization_seconds", serialization_start.elapsed());

    let serialized_start = Instant::now();
    verify_serialized_components(statement, &proof_path, &verifier_path, &public_path)?;
    print_duration("serialized_verify_seconds", serialized_start.elapsed());
    println!("serialized offchain decider verification passed");

    write_manifest(
        &manifest_path,
        batch,
        &nova,
        statement_bytes,
        proof_bytes,
        verifier_bytes,
        public_bytes,
    )?;

    print_u64("statement_bytes", statement_bytes);
    print_u64("proof_bytes", proof_bytes);
    print_u64("verifier_parameter_bytes", verifier_bytes);
    print_u64("public_input_bytes", public_bytes);
    println!("decider proof verification passed");
    println!("decider proof bytes: {proof_bytes}");
    println!("phase 3 artifacts: {}", output_dir.display());
    Ok(())
}

/// Verifies saved artifacts using only public data and a trusted verifier key.
pub fn verify_artifact_dir(
    artifact_dir: impl AsRef<Path>,
    statement_path: impl AsRef<Path>,
    trusted_verifier_path: impl AsRef<Path>,
) -> AppResult<Duration> {
    let started = Instant::now();
    let artifact_dir = artifact_dir.as_ref();
    let external_statement = load_statement(statement_path)?;
    let bundled_statement = load_statement(artifact_dir.join(BUNDLED_STATEMENT))?;
    if bundled_statement != external_statement {
        return Err(invalid(
            "bundled statement does not match the published statement",
        ));
    }
    println!("public statement matched");

    let manifest: Manifest = read_json(artifact_dir.join(MANIFEST_FILE))?;
    validate_manifest(artifact_dir, &manifest, &external_statement)?;

    let bundled_verifier_path = artifact_dir.join(VERIFIER_FILE);
    if fs::read(&bundled_verifier_path)? != fs::read(trusted_verifier_path.as_ref())? {
        return Err(invalid(
            "bundled verifier parameters do not match the trusted copy",
        ));
    }

    verify_serialized_components(
        &external_statement,
        &artifact_dir.join(PROOF_FILE),
        trusted_verifier_path.as_ref(),
        &artifact_dir.join(PUBLIC_FILE),
    )?;
    println!("public recursive state matched");
    println!("offchain decider proof verified");

    let elapsed = started.elapsed();
    print_duration("standalone_verify_seconds", elapsed);
    println!("VERIFICATION PASSED");
    Ok(elapsed)
}

/// Reconstructs verifier-visible initial and terminal states.
pub(crate) fn statement_states(statement: &CommitmentStatement) -> AppResult<(Vec<Fr>, Vec<Fr>)> {
    let count = u64::try_from(statement.obligation_count)
        .map_err(|_| invalid("obligation count does not fit in u64"))?;
    let model = parse_field("model commitment", &statement.model_commitment)?;
    let certificate = parse_field("certificate commitment", &statement.certificate_commitment)?;
    let blinding = parse_field(
        "model blinding commitment",
        &statement.model_blinding_commitment,
    )?;
    let initial = vec![
        Fr::from(0_u64),
        Fr::from(count),
        model,
        certificate,
        model_seed(statement.obligation_count, statement.bound),
        certificate_seed(statement.obligation_count, statement.bound),
        Fr::from(statement.bound),
        blinding,
    ];
    let final_state = vec![
        Fr::from(count),
        Fr::from(count),
        model,
        certificate,
        model,
        certificate,
        Fr::from(statement.bound),
        blinding,
    ];
    Ok((initial, final_state))
}

fn verify_live_decider(
    verifier: &StoredVerifier,
    proof: &StoredProof,
    nova: &NovaScheme,
) -> AppResult<()> {
    verify_values(
        verifier.clone(),
        proof,
        nova.i,
        nova.z_0.clone(),
        nova.z_i.clone(),
        nova.U_i.get_commitments(),
        nova.u_i.get_commitments(),
    )
}

fn verify_serialized_components(
    statement: &CommitmentStatement,
    proof_path: &Path,
    verifier_path: &Path,
    public_path: &Path,
) -> AppResult<()> {
    let proof: StoredProof = read_compressed(proof_path)?;
    let verifier: StoredVerifier = read_compressed(verifier_path)?;
    let public = read_public_packet(public_path)?;
    let (expected_initial, expected_final) = statement_states(statement)?;

    if public.i != Fr::from(statement.obligation_count as u64)
        || public.z_0 != expected_initial
        || public.z_i != expected_final
    {
        return Err(invalid(
            "serialized public inputs do not match the public statement",
        ));
    }

    verify_values(
        verifier,
        &proof,
        public.i,
        public.z_0,
        public.z_i,
        public.running_commitments,
        public.incoming_commitments,
    )
}

fn verify_values(
    verifier: StoredVerifier,
    proof: &StoredProof,
    i: Fr,
    z_0: Vec<Fr>,
    z_i: Vec<Fr>,
    running_commitments: Vec<G1>,
    incoming_commitments: Vec<G1>,
) -> AppResult<()> {
    let verified = FinalDecider::verify(
        verifier,
        i,
        z_0,
        z_i,
        &running_commitments,
        &incoming_commitments,
        proof,
    )?;
    if !verified {
        return Err(invalid("offchain decider verification returned false"));
    }
    Ok(())
}

fn write_public_packet(path: &Path, nova: &NovaScheme) -> AppResult<u64> {
    let file = fs::File::create(path)?;
    let mut writer = BufWriter::new(file);
    nova.i.serialize_compressed(&mut writer)?;
    nova.z_0.serialize_compressed(&mut writer)?;
    nova.z_i.serialize_compressed(&mut writer)?;
    nova.U_i
        .get_commitments()
        .serialize_compressed(&mut writer)?;
    nova.u_i
        .get_commitments()
        .serialize_compressed(&mut writer)?;
    writer.flush()?;
    drop(writer);
    Ok(fs::metadata(path)?.len())
}

fn read_public_packet(path: &Path) -> AppResult<PublicPacket> {
    let bytes = fs::read(path)?;
    let mut remaining = bytes.as_slice();
    let packet = PublicPacket {
        i: Fr::deserialize_compressed(&mut remaining)?,
        z_0: Vec::<Fr>::deserialize_compressed(&mut remaining)?,
        z_i: Vec::<Fr>::deserialize_compressed(&mut remaining)?,
        running_commitments: Vec::<G1>::deserialize_compressed(&mut remaining)?,
        incoming_commitments: Vec::<G1>::deserialize_compressed(&mut remaining)?,
    };
    if !remaining.is_empty() {
        return Err(invalid("public-input packet contains trailing bytes"));
    }
    Ok(packet)
}

fn validate_manifest(
    artifact_dir: &Path,
    manifest: &Manifest,
    statement: &CommitmentStatement,
) -> AppResult<()> {
    if manifest.schema_version != 2
        || manifest.decider != DECIDER_NAME
        || manifest.curve_cycle != CURVE_CYCLE
        || manifest.sonobe_revision != SONOBE_REVISION
        || manifest.zkmc_upstream_commit != ZKMC_UPSTREAM_COMMIT
        || manifest.benchmark != statement.benchmark
        || manifest.obligation_count != statement.obligation_count
        || manifest.bound != statement.bound
        || manifest.model_blinding_commitment != statement.model_blinding_commitment
        || manifest.model_commitment != statement.model_commitment
        || manifest.certificate_commitment != statement.certificate_commitment
    {
        return Err(invalid("manifest does not match the public statement"));
    }
    let (initial, final_state) = statement_states(statement)?;
    if manifest.initial_state != field_strings(&initial)
        || manifest.final_state != field_strings(&final_state)
    {
        return Err(invalid(
            "manifest recursive states do not match the public statement",
        ));
    }
    validate_file(artifact_dir, &manifest.files.statement, BUNDLED_STATEMENT)?;
    validate_file(artifact_dir, &manifest.files.proof, PROOF_FILE)?;
    validate_file(artifact_dir, &manifest.files.verifier, VERIFIER_FILE)?;
    validate_file(artifact_dir, &manifest.files.public_inputs, PUBLIC_FILE)?;
    Ok(())
}

fn validate_file(artifact_dir: &Path, entry: &FileEntry, expected: &str) -> AppResult<()> {
    if entry.name != expected || fs::metadata(artifact_dir.join(expected))?.len() != entry.bytes {
        return Err(invalid("manifest file metadata mismatch"));
    }
    Ok(())
}

fn write_manifest(
    path: &Path,
    batch: &Batch,
    nova: &NovaScheme,
    statement_bytes: u64,
    proof_bytes: u64,
    verifier_bytes: u64,
    public_bytes: u64,
) -> AppResult<()> {
    let manifest = Manifest {
        schema_version: 2,
        benchmark: batch.benchmark.clone(),
        obligation_count: batch.obligations.len(),
        bound: batch.bound,
        decider: DECIDER_NAME.to_string(),
        curve_cycle: CURVE_CYCLE.to_string(),
        model_blinding_commitment: field_string(batch.model_blinding_commitment),
        model_commitment: field_string(batch.model_commitment),
        certificate_commitment: field_string(batch.certificate_commitment),
        initial_state: field_strings(&nova.z_0),
        final_state: field_strings(&nova.z_i),
        sonobe_revision: SONOBE_REVISION.to_string(),
        zkmc_upstream_commit: ZKMC_UPSTREAM_COMMIT.to_string(),
        files: ArtifactFiles {
            statement: FileEntry {
                name: BUNDLED_STATEMENT.to_string(),
                bytes: statement_bytes,
            },
            proof: FileEntry {
                name: PROOF_FILE.to_string(),
                bytes: proof_bytes,
            },
            verifier: FileEntry {
                name: VERIFIER_FILE.to_string(),
                bytes: verifier_bytes,
            },
            public_inputs: FileEntry {
                name: PUBLIC_FILE.to_string(),
                bytes: public_bytes,
            },
        },
    };
    write_json(path, &manifest)
}

fn parse_field(name: &str, value: &str) -> AppResult<Fr> {
    value
        .parse::<Fr>()
        .map_err(|_| invalid(&format!("invalid decimal {name}")))
}

fn field_strings(values: &[Fr]) -> Vec<String> {
    values.iter().map(ToString::to_string).collect()
}

fn field_string(value: Fr) -> String {
    value.to_string()
}

fn invalid(message: &str) -> Box<dyn std::error::Error> {
    io::Error::new(io::ErrorKind::InvalidData, message).into()
}

/// Returns the default artifact directory.
pub fn default_artifact_dir() -> PathBuf {
    PathBuf::from("artifacts/phase3")
}
