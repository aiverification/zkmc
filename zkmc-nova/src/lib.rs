//! Exposes the complete ZKMC proving components.

pub mod artifact;
pub mod checker;
pub mod circuit;
pub mod commitment;
pub mod config;
pub mod decider;
pub mod input;
pub mod metrics;
pub mod model;
pub mod runner;
pub mod statement;

use std::error::Error;

pub type AppResult<T> = Result<T, Box<dyn Error>>;

#[cfg(test)]
mod tests;
