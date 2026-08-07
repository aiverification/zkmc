//! Defines generated circuit sizing constants.

include!("generated_config.rs");

pub const DEFAULT_INPUT: &str = "examples/obligations.json";
pub const MODEL_BLINDING_BITS: usize = 64;

/// Returns the representable inclusive integer bound.
pub fn max_bound() -> u64 {
    if RANGE_BITS >= u64::BITS as usize {
        u64::MAX
    } else {
        (1_u64 << RANGE_BITS) - 1
    }
}
