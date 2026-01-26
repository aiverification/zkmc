# Benchmark Programs

This directory contains `.gc` program files used for performance benchmarking of zkterm-tool.

## Directory Structure

```
programs/
├── README.md                        # This file
├── counter_simple.gc                # Baseline benchmark (simple counter)
```

## Benchmark Programs

### Baseline Benchmarks

**counter_simple.gc**
- Simple counter that increments from 0 to maxVal
- Used to establish baseline performance
- Configurable via `maxVal` constant override
- Small state space for quick validation

## Adding Custom Benchmarks

### Method 1: Add a standalone .gc file

1. Create your `.gc` file in this directory:
   ```
   benchmarks/programs/my_protocol.gc
   ```

2. Add a `BenchmarkCase` to `benchmark_config.py`:
   ```python
   BenchmarkCase(
       name="my_protocol",
       program_file="my_protocol.gc",
       const_overrides={},
       bounds=["var1:0:100", "var2:0:50"],
       tags=["custom"],
       description="My custom protocol benchmark"
   )
   ```
   
## Bounds Specification

For zkexplicit benchmarks, you must specify variable bounds in the format `"var:min:max"`:

```python
bounds=["x:0:10", "y:0:5", "status:0:3"]
# Means: x ∈ [0, 10], y ∈ [0, 5], status ∈ [0, 3]
```

**Important**: Bounds should be slightly larger than the actual variable range to catch potential bugs where variables exceed expected ranges.

Example: If `maxVal = 10`, use `bounds=["x:0:15"]` to include a safety margin.

## Tags for Organization

Use tags to organize and filter benchmarks:

- `"baseline"` - Simple benchmarks for establishing baseline performance
- `"small"` - Quick benchmarks for validation
- `"medium"` - Moderate size for typical performance measurement
- `"large"` - Stress tests for scalability analysis
- `"paper"` - Benchmarks included in academic publications
- `"custom"` - User-added benchmarks
- Family names (e.g., `"exp_backoff"`) - Group related variants

Filter by tag when running benchmarks:
```bash
# Run only small benchmarks
uv run pytest benchmarks/ --benchmark-only -k "small"

# Run only paper benchmarks
uv run pytest benchmarks/ --benchmark-only -m "paper"
```

## Performance Characteristics

### State Space Growth

- **Linear variable**: range r → O(r) states
- **Two independent variables**: ranges r₁, r₂ → O(r₁ × r₂) states
- **n variables**: ranges r₁...rₙ → O(r₁ × ... × rₙ) states

### zkexplicit Complexity

- **State enumeration**: O(r₁ × ... × rₙ)
- **Violation checking**: O((r₁ × ... × rₙ)² × |δ|) where |δ| = number of automaton transitions
- **Embeddings**: O(r₁ × ... × rₙ) for states, O((r₁ × ... × rₙ)²) for transitions

Example: 2 variables with range 100 each
- States: 100² = 10,000
- State pairs for violation checking: 100⁴ = 100,000,000

### zkverify Complexity

- **Obligation count**: O(|program_trans| × |automaton_trans| × |cases|)
- **Z3 solving**: Varies based on constraint complexity, typically dominates total time
- **Parsing + encoding**: Usually negligible compared to Z3 time

## Best Practices

1. **Start small**: Test with small bounds first, then scale up
2. **Include safety margins**: Bounds should exceed actual variable ranges by 10-50%
3. **Document constants**: Use descriptive names and document their meaning
4. **Tag appropriately**: Use tags for filtering and organization
5. **Provide descriptions**: Clear descriptions help understand benchmark purpose
6. **Parametric families**: Use templates for scaling experiments
7. **Verification sanity check**: Ensure programs actually verify correctly before benchmarking

## Example Benchmark Session

```bash
# Validate benchmark programs work correctly
uv run pytest benchmarks/ --benchmark-only -k "counter_small" --benchmark-verbose

# Run all baseline benchmarks
uv run pytest benchmarks/ --benchmark-only -k "baseline"

# Run full benchmark suite with statistical rigor
uv run pytest benchmarks/ --benchmark-only --benchmark-min-rounds=20

# Export results for paper
uv run pytest benchmarks/ --benchmark-only -k "paper" --benchmark-json=results.json

# Compare before/after optimization
uv run pytest benchmarks/ --benchmark-only --benchmark-save=before
# ... make optimizations ...
uv run pytest benchmarks/ --benchmark-only --benchmark-compare=before
```

## Notes

- All benchmark programs must include automaton transitions (required for verification)
- Programs should verify correctly (no violations) for meaningful benchmarks
- Template substitution is simple string replacement (no complex logic)
- Bounds are inclusive: `x:0:10` means x ∈ {0, 1, 2, ..., 10}
