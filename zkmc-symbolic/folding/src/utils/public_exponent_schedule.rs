/// Returns the first `n` terms of the progression-free (Salem-Spencer)
/// exponent schedule, starting from 0.
///
/// Construction: assign index `m` (0..n) its binary representation using
/// k = ceil(log2(n)) bits, then reinterpret those same 0/1 digits as a
/// base-3 number. Since digits are only 0/1, base-3 addition of two
/// terms never carries, which is what guarantees 2C ∩ K = ∅.
pub fn salem_spencer_schedule(n: usize) -> Vec<u128> {
    if n == 0 {
        return Vec::new();
    }
    // k = number of bits needed to represent n - 1 (i.e. ceil(log2(n)))
    let k = if n > 1 {
        (usize::BITS - (n - 1).leading_zeros()) as u32
    } else {
        0
    };

    (0..n as u64)
        .map(|m| {
            let mut c: u128 = 0;
            for bit in 0..k {
                if (m >> bit) & 1 == 1 {
                    c += 3u128.pow(bit);
                }
            }
            c
        })
        .collect()
}

/// Returns the restricted sumset K = C +̂ C = {c_i + c_j : i != j},
/// keeping only distinct values, sorted ascending.
pub fn compute_k_values(schedule: &[u128]) -> Vec<u128> {
    let mut set: std::collections::BTreeSet<u128> = std::collections::BTreeSet::new();
    for i in 0..schedule.len() {
        for j in 0..schedule.len() {
            if i != j {
                set.insert(schedule[i] + schedule[j]);
            }
        }
    }
    set.into_iter().collect()
}

/// Checks the required schedule property 2C ∩ K = ∅ (equivalently
/// 2c_i != c_j + c_k for all i and all j != k).
pub fn schedule_has_no_2c_collisions(schedule: &[u128]) -> bool {
    let doubled: std::collections::BTreeSet<u128> = schedule.iter().map(|c| 2 * c).collect();
    compute_k_values(schedule).iter().all(|k| !doubled.contains(k))
}
/// The pairs (i, j), i != j, grouped by their cross-term exponent
/// k = c_i + c_j, in ascending order of k.
///
/// Building this once and threading it through replaces four independent
/// O(N^2) `HashMap` constructions (one per multiplication family) plus a
/// fifth inside `compute_k_values`.
pub struct CrossTermPairs {
    /// Distinct k values, ascending.
    pub k_values: Vec<u128>,
    /// `pairs[idx]` holds every (i, j) with c_i + c_j == k_values[idx].
    pub pairs: Vec<Vec<(usize, usize)>>,
}

impl CrossTermPairs {
    pub fn new(schedule: &[u128]) -> Self {
        let n = schedule.len();
        let mut by_k: std::collections::BTreeMap<u128, Vec<(usize, usize)>> =
            std::collections::BTreeMap::new();
        for i in 0..n {
            for j in 0..n {
                if i != j {
                    by_k.entry(schedule[i] + schedule[j]).or_default().push((i, j));
                }
            }
        }
        let mut k_values = Vec::with_capacity(by_k.len());
        let mut pairs = Vec::with_capacity(by_k.len());
        for (k, group) in by_k {
            k_values.push(k);
            pairs.push(group);
        }
        Self { k_values, pairs }
    }

    pub fn len(&self) -> usize {
        self.k_values.len()
    }

    /// For each k, one representative (i, j) with c_i + c_j == k.
    ///
    /// Lets r^k be formed as r^{c_i} * r^{c_j} -- a single field
    /// multiplication instead of a modular exponentiation.
    pub fn representatives(&self) -> Vec<(usize, usize)> {
        self.pairs.iter().map(|group| group[0]).collect()
    }
}
