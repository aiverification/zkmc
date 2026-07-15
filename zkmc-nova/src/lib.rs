//! Exposes Phase Two ZKMC components.

pub mod checker;
pub mod circuit;
pub mod config;
pub mod input;
pub mod model;
pub mod runner;

use std::error::Error;

pub type AppResult<T> = Result<T, Box<dyn Error>>;

#[cfg(test)]
mod tests;
