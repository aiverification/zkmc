//! Reads and writes proof artifacts without accepting trailing data.

use crate::AppResult;
use ark_serialize::{CanonicalDeserialize, CanonicalSerialize};
use serde::{de::DeserializeOwned, Serialize};
use std::{fs, io, path::Path};

/// Writes one canonically compressed value.
pub fn write_compressed<T: CanonicalSerialize>(
    path: impl AsRef<Path>,
    value: &T,
) -> AppResult<u64> {
    let path = path.as_ref();
    let mut bytes = Vec::new();
    value.serialize_compressed(&mut bytes)?;
    fs::write(path, bytes)?;
    Ok(fs::metadata(path)?.len())
}

/// Reads exactly one canonically compressed value.
pub fn read_compressed<T: CanonicalDeserialize>(path: impl AsRef<Path>) -> AppResult<T> {
    let bytes = fs::read(path)?;
    let mut remaining = bytes.as_slice();
    let value = T::deserialize_compressed(&mut remaining)?;
    if !remaining.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "compressed artifact contains trailing bytes",
        )
        .into());
    }
    Ok(value)
}

/// Writes one formatted JSON value.
pub fn write_json<T: Serialize>(path: impl AsRef<Path>, value: &T) -> AppResult<()> {
    fs::write(path, serde_json::to_string_pretty(value)? + "\n")?;
    Ok(())
}

/// Reads one JSON value.
pub fn read_json<T: DeserializeOwned>(path: impl AsRef<Path>) -> AppResult<T> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}
