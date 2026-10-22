//! Emits stable machine-readable performance metrics.

use std::time::Duration;

/// Prints one duration in seconds for benchmark collection.
pub fn print_duration(name: &str, duration: Duration) {
    println!("METRIC {name}={:.9}", duration.as_secs_f64());
}

/// Prints one integer metric for benchmark collection.
pub fn print_u64(name: &str, value: u64) {
    println!("METRIC {name}={value}");
}

/// Prints one floating-point metric for benchmark collection.
pub fn print_f64(name: &str, value: f64) {
    println!("METRIC {name}={value:.9}");
}
