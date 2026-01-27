#!/usr/bin/env python3
"""Parse pytest-benchmark JSON files and create comparison tables."""

import json
import csv
from pathlib import Path
from typing import List, Dict, Any


def parse_benchmark_json(filepath: Path) -> List[Dict[str, Any]]:
    """Parse a pytest-benchmark JSON file and extract key metrics."""
    with open(filepath) as f:
        data = json.load(f)

    results = []
    for bench in data['benchmarks']:
        # Extract test name and parameters
        name = bench['name']
        group = bench.get('group', 'unknown')
        params = bench.get('params', {})

        # Extract statistics
        stats = bench['stats']

        result = {
            'name': name,
            'group': group,
            'min': stats['min'],
            'max': stats['max'],
            'mean': stats['mean'],
            'stddev': stats['stddev'],
            'median': stats['median'],
            'iqr': stats['iqr'],
            'rounds': stats['rounds'],
            'iterations': stats['iterations'],
        }

        # Add parameter info if available
        for param_name, param_value in params.items():
            result[f'param_{param_name}'] = param_value

        results.append(result)

    return results


def parse_all_benchmarks(file_pattern: str = "*.json") -> Dict[str, List[Dict[str, Any]]]:
    """Parse all benchmark JSON files in current directory."""
    benchmarks = {}

    cwd = Path.cwd()
    for json_file in cwd.glob(file_pattern):
        # Skip non-benchmark JSONs
        if json_file.name in ['results.json', 'farkas_test.json']:
            continue

        print(f"Parsing {json_file.name}...")
        benchmarks[json_file.stem] = parse_benchmark_json(json_file)

    return benchmarks


def create_csv_table(benchmarks: Dict[str, List[Dict[str, Any]]], output_file: str = "benchmark_results.csv"):
    """Create a CSV file with all benchmark results."""
    # Collect all unique column names
    columns = set(['source', 'name', 'group'])
    for results in benchmarks.values():
        for result in results:
            columns.update(result.keys())

    columns = sorted(columns)

    # Write CSV
    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for source, results in benchmarks.items():
            for result in results:
                row = {'source': source, **result}
                writer.writerow(row)

    print(f"CSV table written to {output_file}")


def create_html_table(benchmarks: Dict[str, List[Dict[str, Any]]], output_file: str = "benchmark_results.html"):
    """Create an HTML file with formatted benchmark results."""

    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Benchmark Results</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
        }
        h2 {
            color: #666;
            margin-top: 30px;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            background-color: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }
        th {
            background-color: #4CAF50;
            color: white;
            padding: 12px;
            text-align: left;
            position: sticky;
            top: 0;
        }
        td {
            padding: 10px;
            border-bottom: 1px solid #ddd;
        }
        tr:hover {
            background-color: #f5f5f5;
        }
        .number {
            text-align: right;
            font-family: 'Courier New', monospace;
        }
        .source {
            font-weight: bold;
            color: #4CAF50;
        }
        .summary {
            background-color: #fff3cd;
            padding: 15px;
            margin: 20px 0;
            border-left: 4px solid #ffc107;
        }
    </style>
</head>
<body>
    <h1>Benchmark Results Comparison</h1>
"""

    # Add summary section
    total_benchmarks = sum(len(results) for results in benchmarks.values())
    html += f"""
    <div class="summary">
        <strong>Summary:</strong>
        <ul>
            <li>Total benchmark files: {len(benchmarks)}</li>
            <li>Total benchmarks: {total_benchmarks}</li>
            <li>Files: {', '.join(benchmarks.keys())}</li>
        </ul>
    </div>
"""

    # Create table for each source file
    for source, results in sorted(benchmarks.items()):
        html += f"""
    <h2>{source}</h2>
    <table>
        <thead>
            <tr>
                <th>Name</th>
                <th>Group</th>
                <th>Mean (s)</th>
                <th>Min (s)</th>
                <th>Max (s)</th>
                <th>Median (s)</th>
                <th>Std Dev (s)</th>
                <th>IQR (s)</th>
                <th>Rounds</th>
            </tr>
        </thead>
        <tbody>
"""

        for result in results:
            html += f"""
            <tr>
                <td>{result['name']}</td>
                <td>{result.get('group', 'N/A')}</td>
                <td class="number">{result['mean']:.6f}</td>
                <td class="number">{result['min']:.6f}</td>
                <td class="number">{result['max']:.6f}</td>
                <td class="number">{result['median']:.6f}</td>
                <td class="number">{result['stddev']:.6f}</td>
                <td class="number">{result['iqr']:.6f}</td>
                <td class="number">{result['rounds']}</td>
            </tr>
"""

        html += """
        </tbody>
    </table>
"""

    html += """
</body>
</html>
"""

    with open(output_file, 'w') as f:
        f.write(html)

    print(f"HTML table written to {output_file}")


def create_comparison_table(benchmarks: Dict[str, List[Dict[str, Any]]], output_file: str = "benchmark_comparison.html"):
    """Create side-by-side comparison of same benchmarks across different runs."""

    # Group benchmarks by name
    by_name = {}
    for source, results in benchmarks.items():
        for result in results:
            name = result['name']
            if name not in by_name:
                by_name[name] = {}
            by_name[name][source] = result

    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Benchmark Comparison</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #333;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            background-color: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        th {
            background-color: #2196F3;
            color: white;
            padding: 12px;
            text-align: left;
            position: sticky;
            top: 0;
        }
        td {
            padding: 10px;
            border-bottom: 1px solid #ddd;
        }
        tr:hover {
            background-color: #f5f5f5;
        }
        .number {
            text-align: right;
            font-family: 'Courier New', monospace;
        }
        .benchmark-name {
            font-weight: bold;
            background-color: #e3f2fd;
        }
        .faster {
            color: #4CAF50;
            font-weight: bold;
        }
        .slower {
            color: #f44336;
            font-weight: bold;
        }
    </style>
</head>
<body>
    <h1>Benchmark Comparison: Side-by-Side</h1>
    <p>Comparing mean execution times across different benchmark runs.</p>

    <table>
        <thead>
            <tr>
                <th>Benchmark Name</th>
"""

    # Add column headers for each source
    sources = sorted(benchmarks.keys())
    for source in sources:
        html += f"                <th>{source} Mean (s)</th>\n"

    html += """
            </tr>
        </thead>
        <tbody>
"""

    # Add rows for each benchmark
    for name in sorted(by_name.keys()):
        results_by_source = by_name[name]

        html += f"""
            <tr>
                <td class="benchmark-name">{name}</td>
"""

        # Collect all mean times to find min/max for highlighting
        means = [results_by_source[src]['mean'] for src in sources if src in results_by_source]
        min_mean = min(means) if means else 0
        max_mean = max(means) if means else 0

        for source in sources:
            if source in results_by_source:
                mean = results_by_source[source]['mean']

                # Highlight fastest/slowest
                css_class = "number"
                if len(means) > 1:
                    if mean == min_mean:
                        css_class += " faster"
                    elif mean == max_mean:
                        css_class += " slower"

                html += f'                <td class="{css_class}">{mean:.6f}</td>\n'
            else:
                html += '                <td class="number">-</td>\n'

        html += "            </tr>\n"

    html += """
        </tbody>
    </table>
</body>
</html>
"""

    with open(output_file, 'w') as f:
        f.write(html)

    print(f"Comparison table written to {output_file}")


if __name__ == "__main__":
    import sys

    # Parse command line arguments
    pattern = sys.argv[1] if len(sys.argv) > 1 else "*verify*.json"

    print(f"Looking for benchmark files matching: {pattern}")
    benchmarks = parse_all_benchmarks(pattern)

    if not benchmarks:
        print("No benchmark files found!")
        sys.exit(1)

    print(f"\nFound {len(benchmarks)} benchmark file(s)")
    print(f"Total benchmarks: {sum(len(r) for r in benchmarks.values())}")

    # Create outputs
    create_csv_table(benchmarks)
    create_html_table(benchmarks)
    create_comparison_table(benchmarks)

    print("\n✓ Done! Open benchmark_results.html or benchmark_comparison.html in your browser")
