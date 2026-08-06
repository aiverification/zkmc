//! Produces and verifies hiding compressed Nova proofs.

use crate::{
    AppResult,
    artifact::{read_compressed, read_json, write_compressed, write_json},
    circuit::ZkmcCircuit,
    commitment::{certificate_seed, model_seed},
    metrics::{print_duration, print_u64},
    model::Batch,
    statement::{BUNDLED_STATEMENT, CommitmentStatement, load_statement},
};
use ark_bn254::{Bn254, Fr, G1Projective as G1};
use ark_crypto_primitives::sponge::poseidon::PoseidonSponge;
use ark_grumpkin::Projective as G2;
use ark_serialize::{CanonicalDeserialize, CanonicalSerialize};
use serde::{Deserialize, Serialize};
use sonobe_fs::nova::{CycleFoldNova, Nova};
use sonobe_ivc::{
    IVCProofCompressor, IVCTypes,
    compilers::cyclefold::{CycleFoldBasedIVCDecider, adapters::nova::NovaNovaIVC},
};
use sonobe_primitives::commitments::{CommitmentDef, pedersen::Pedersen};
use sonobe_snarks::cp::legogroth16::LegoGroth16;
use std::{
    fs,
    io::{self, BufWriter, Write},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

type PrimaryCommitment = Pedersen<G1, true>;
type SecondaryCommitment = Pedersen<G2, true>;

const _: () = assert!(PrimaryCommitment::IS_HIDING);
const _: () = assert!(SecondaryCommitment::IS_HIDING);

pub type PrimaryNova = Nova<PrimaryCommitment>;
pub type SecondaryNova = CycleFoldNova<SecondaryCommitment>;
pub type NovaScheme = NovaNovaIVC<PrimaryCommitment, SecondaryCommitment, PoseidonSponge<Fr>>;
pub type FinalDecider =
    CycleFoldBasedIVCDecider<PrimaryNova, SecondaryNova, PoseidonSponge<Fr>, LegoGroth16<Bn254>>;

pub type NovaProverKey = <NovaScheme as IVCTypes>::ProverKey<ZkmcCircuit>;
pub type NovaVerifierKey = <NovaScheme as IVCTypes>::VerifierKey<ZkmcCircuit>;
pub type NovaProof = <NovaScheme as IVCTypes>::Proof<ZkmcCircuit>;

pub struct NovaParams {
    pub prover_key: NovaProverKey,
    pub verifier_key: NovaVerifierKey,
}

pub struct NovaRun {
    pub i: usize,
    pub z_0: [Fr; 8],
    pub z_i: [Fr; 8],
    pub proof: NovaProof,
}

type StoredProof = <FinalDecider as IVCProofCompressor>::CompressedProof<ZkmcCircuit>;
type StoredVerifier = <FinalDecider as IVCProofCompressor>::VerifierKey<ZkmcCircuit>;

pub const PROOF_FILE: &str = "decider_proof.bin";
pub const VERIFIER_FILE: &str = "decider_verifier.bin";
pub const PUBLIC_FILE: &str = "decider_public.bin";
pub const MANIFEST_FILE: &str = "manifest.json";
const DECIDER_NAME: &str = "Sonobe CycleFold LegoGroth16 decider";
const CURVE_CYCLE: &str = "BN254/Grumpkin";
const PROTOCOL_VERSION: &str = "zkmc-nova-pedersen-legogroth16-v1";
const SONOBE_REVISION: &str = "243391ebc14ad993f425802eb9dbaf44fdd54436";

#[derive(Debug, Deserialize, Serialize)]
struct Manifest {
    schema_version: u32,
    protocol_version: String,
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
    source_revision: Option<String>,
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
    i: u64,
    z_0: [Fr; 8],
    z_i: [Fr; 8],
}

/// Generates, verifies, and stores the final proof.
pub fn finalize_decider(
    batch: &Batch,
    statement: &CommitmentStatement,
    nova_params: NovaParams,
    circuit: ZkmcCircuit,
    nova: NovaRun,
    output_dir: impl AsRef<Path>,
) -> AppResult<()> {
    let output_dir = output_dir.as_ref();
    fs::create_dir_all(output_dir)?;
    let mut rng = ark_std::rand::rngs::OsRng;

    let setup_start = Instant::now();
    let NovaParams { verifier_key, .. } = nova_params;
    let (decider_pp, decider_vp) =
        FinalDecider::preprocess_and_generate_keys(&circuit, verifier_key, &mut rng)?;
    let setup_elapsed = setup_start.elapsed();
    println!("LegoGroth16 decider setup completed in {setup_elapsed:?}");
    print_duration("decider_setup_seconds", setup_elapsed);

    let prove_start = Instant::now();
    let proof = FinalDecider::prove::<ZkmcCircuit>(
        &decider_pp,
        nova.i,
        &nova.z_0,
        &nova.z_i,
        &nova.proof,
        &mut rng,
    )?;
    let prove_elapsed = prove_start.elapsed();
    println!("LegoGroth16 decider proof generated in {prove_elapsed:?}");
    print_duration("decider_prove_seconds", prove_elapsed);

    let verify_start = Instant::now();
    verify_live_decider(&decider_vp, &proof, &nova)?;
    let verify_elapsed = verify_start.elapsed();
    print_duration("in_memory_verify_seconds", verify_elapsed);
    println!("in-memory LegoGroth16 decider verification passed");

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
    println!("serialized LegoGroth16 decider verification passed");

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

#[cfg(test)]
/// Checks repeated compressed proofs differ.
pub(crate) fn repeated_compressed_proofs_differ(
    nova_params: NovaParams,
    circuit: &ZkmcCircuit,
    nova: &NovaRun,
) -> AppResult<bool> {
    let mut rng = ark_std::rand::rngs::OsRng;
    let NovaParams { verifier_key, .. } = nova_params;
    let (decider_pp, decider_vp) =
        FinalDecider::preprocess_and_generate_keys(circuit, verifier_key, &mut rng)?;
    let first = FinalDecider::prove::<ZkmcCircuit>(
        &decider_pp,
        nova.i,
        &nova.z_0,
        &nova.z_i,
        &nova.proof,
        &mut rng,
    )?;
    let second = FinalDecider::prove::<ZkmcCircuit>(
        &decider_pp,
        nova.i,
        &nova.z_0,
        &nova.z_i,
        &nova.proof,
        &mut rng,
    )?;
    verify_live_decider(&decider_vp, &first, nova)?;
    verify_live_decider(&decider_vp, &second, nova)?;

    let mut first_bytes = Vec::new();
    let mut second_bytes = Vec::new();
    first.serialize_compressed(&mut first_bytes)?;
    second.serialize_compressed(&mut second_bytes)?;
    Ok(first_bytes != second_bytes)
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
    println!("LegoGroth16 decider proof verified");

    let elapsed = started.elapsed();
    print_duration("standalone_verify_seconds", elapsed);
    println!("VERIFICATION PASSED");
    Ok(elapsed)
}

/// Reconstructs verifier-visible initial and terminal states.
pub(crate) fn statement_states(statement: &CommitmentStatement) -> AppResult<([Fr; 8], [Fr; 8])> {
    let count = u64::try_from(statement.obligation_count)
        .map_err(|_| invalid("obligation count does not fit in u64"))?;
    let model = parse_field("model commitment", &statement.model_commitment)?;
    let certificate = parse_field("certificate commitment", &statement.certificate_commitment)?;
    let blinding = parse_field(
        "model blinding commitment",
        &statement.model_blinding_commitment,
    )?;
    let initial = [
        Fr::from(0_u64),
        Fr::from(count),
        model,
        certificate,
        model_seed(statement.obligation_count, statement.bound),
        certificate_seed(statement.obligation_count, statement.bound),
        Fr::from(statement.bound),
        blinding,
    ];
    let final_state = [
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
    nova: &NovaRun,
) -> AppResult<()> {
    verify_values(verifier, proof, nova.i, &nova.z_0, &nova.z_i)
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

    if public.i != u64::try_from(statement.obligation_count)?
        || public.z_0 != expected_initial
        || public.z_i != expected_final
    {
        return Err(invalid(
            "serialized public inputs do not match the public statement",
        ));
    }

    verify_values(
        &verifier,
        &proof,
        public.i as usize,
        &public.z_0,
        &public.z_i,
    )
}

fn verify_values(
    verifier: &StoredVerifier,
    proof: &StoredProof,
    i: usize,
    z_0: &[Fr; 8],
    z_i: &[Fr; 8],
) -> AppResult<()> {
    FinalDecider::verify::<ZkmcCircuit>(verifier, i, z_0, z_i, proof)?;
    Ok(())
}

fn write_public_packet(path: &Path, nova: &NovaRun) -> AppResult<u64> {
    let file = fs::File::create(path)?;
    let mut writer = BufWriter::new(file);
    let i = u64::try_from(nova.i).map_err(|_| invalid("step count does not fit in u64"))?;
    i.serialize_compressed(&mut writer)?;
    nova.z_0.serialize_compressed(&mut writer)?;
    nova.z_i.serialize_compressed(&mut writer)?;
    writer.flush()?;
    drop(writer);
    Ok(fs::metadata(path)?.len())
}

fn read_public_packet(path: &Path) -> AppResult<PublicPacket> {
    let bytes = fs::read(path)?;
    let mut remaining = bytes.as_slice();
    let packet = PublicPacket {
        i: u64::deserialize_compressed(&mut remaining)?,
        z_0: <[Fr; 8]>::deserialize_compressed(&mut remaining)?,
        z_i: <[Fr; 8]>::deserialize_compressed(&mut remaining)?,
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
    if manifest.schema_version != 3
        || manifest.protocol_version != PROTOCOL_VERSION
        || manifest.decider != DECIDER_NAME
        || manifest.curve_cycle != CURVE_CYCLE
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
    nova: &NovaRun,
    statement_bytes: u64,
    proof_bytes: u64,
    verifier_bytes: u64,
    public_bytes: u64,
) -> AppResult<()> {
    let manifest = Manifest {
        schema_version: 3,
        protocol_version: PROTOCOL_VERSION.to_string(),
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
        source_revision: option_env!("ZKMC_SOURCE_REVISION").map(str::to_owned),
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
    let parsed = value
        .parse::<Fr>()
        .map_err(|_| invalid(&format!("invalid decimal {name}")))?;
    if parsed.to_string() != value {
        return Err(invalid(&format!("non-canonical decimal {name}")));
    }
    Ok(parsed)
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
