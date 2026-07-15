//! Groups circuit inputs and constraints.

mod constraints;
mod input;

pub use constraints::{circuit_satisfied, initial_state, ZkmcCircuit};
pub use input::{as_input, SignedInput, SignedVar, StepInput, StepInputVar};
