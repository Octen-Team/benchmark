#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Multi-API Performance Test Results Analyzer

Analyzes JSONL test results and generates comprehensive reports:
- Success rates and error distributions
- Latency percentiles (P50, P90, P99)
- Error categorization by type
- Comparison tables across APIs and QPS levels

Outputs:
- results/summary.json (machine-readable)
- results/summary.txt (human-readable)
- results/latency_comparison.csv (spreadsheet import)
"""

import json
import csv
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict, Counter
import statistics


RESULTS_DIR = "results"
ERROR_TYPES = ["timeout", "rate_limit", "connection_error", "api_error", "validation_error"]


def calculate_percentiles(values: List[float]) -> Dict[str, float]:
    """Calculate P50, P90, P99 percentiles."""
    if not values:
        return {"p50": 0, "p90": 0, "p99": 0}

    sorted_values = sorted(values)
    n = len(sorted_values)

    def percentile(p):
        k = (n - 1) * p / 100
        f = int(k)
        c = f + 1
        if c >= n:
            return sorted_values[-1]
        d0 = sorted_values[f]
        d1 = sorted_values[c]
        return d0 + (d1 - d0) * (k - f)

    # Return values in milliseconds (ms), rounded to 1 decimal place
    return {
        "p50": round(percentile(50) * 1000, 1),
        "p90": round(percentile(90) * 1000, 1),
        "p99": round(percentile(99) * 1000, 1),
    }


def analyze_jsonl(file_path: str) -> Dict[str, Any]:
    """
    Analyze a single JSONL results file.

    Returns analysis dict with:
    - total_requests
    - successful_requests
    - success_rate
    - error_count
    - error_by_type
    - error_by_status
    - latency_percentiles (total_time and api_time)
    - actual_qps
    """
    if not Path(file_path).exists():
        return None

    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    record = json.loads(line)
                    records.append(record)
                except json.JSONDecodeError:
                    continue

    if not records:
        return None

    # Basic counts
    total = len(records)
    successful = sum(1 for r in records if r.get("status") == 200)
    success_rate = (successful / total * 100) if total > 0 else 0

    # Error analysis
    errors = [r for r in records if r.get("status") != 200]
    error_count = len(errors)

    # Error by type
    error_by_type = Counter()
    for r in errors:
        error_type = r.get("error_type", "unknown")
        error_by_type[error_type] += 1

    # Error by status code
    error_by_status = Counter()
    for r in errors:
        status = r.get("status")
        if status and status != 200:
            error_by_status[status] += 1

    # Latency analysis - only count successful requests (status=200)
    # Latency from failed requests does not reflect true API performance
    successful_records = [r for r in records if r.get("status") == 200]
    total_times = [r["total_time"] for r in successful_records if r.get("total_time")]
    api_times = [r["api_time"] for r in successful_records if r.get("api_time")]

    total_time_percentiles = calculate_percentiles(total_times)
    api_time_percentiles = calculate_percentiles(api_times)

    # Calculate actual QPS from timestamps
    if len(records) >= 2:
        from datetime import datetime
        try:
            first_ts = datetime.fromisoformat(records[0]["timestamp"].replace('Z', '+00:00'))
            last_ts = datetime.fromisoformat(records[-1]["timestamp"].replace('Z', '+00:00'))
            duration = (last_ts - first_ts).total_seconds()
            actual_qps = (total - 1) / duration if duration > 0 else 0
        except Exception:
            actual_qps = 0
    else:
        actual_qps = 0

    return {
        "total_requests": total,
        "successful_requests": successful,
        "success_rate": round(success_rate, 2),
        "error_count": error_count,
        "error_rate": round((error_count / total * 100) if total > 0 else 0, 2),
        "error_by_type": dict(error_by_type),
        "error_by_status": dict(error_by_status),
        "total_time_percentiles": total_time_percentiles,
        "api_time_percentiles": api_time_percentiles,
        "actual_qps": round(actual_qps, 2),
    }


def load_all_results(results_dir: str, mode: Optional[str] = None, qps_filter: Optional[List[int]] = None) -> Dict[str, Dict[str, Any]]:
    """
    Load and analyze all JSONL files in results directory.

    Args:
        results_dir: Directory containing JSONL result files
        mode: Filter by result type — "serial", "qps", or None (all)
        qps_filter: When mode="qps", only include these QPS levels (None = all)

    Returns nested dict: {api: {qps: analysis_dict}}
    """
    results_path = Path(results_dir)
    if not results_path.exists():
        raise FileNotFoundError(f"Results directory not found: {results_dir}")

    all_results = defaultdict(dict)

    # Find all JSONL files
    jsonl_files = list(results_path.glob("*.jsonl"))

    if not jsonl_files:
        raise ValueError(f"No JSONL files found in {results_dir}")

    print(f"Found {len(jsonl_files)} result files")

    # Analyze each file
    for file_path in jsonl_files:
        filename = file_path.stem  # Remove .jsonl

        # Parse filename: {api}_serial.jsonl or {api}_qps{qps}.jsonl
        if filename.endswith('_serial'):
            if mode == "qps":
                continue
            api = filename[:-7]  # strip '_serial'
            qps = 'serial'
        elif '_qps' in filename:
            if mode == "serial":
                continue
            parts = filename.split('_qps')
            if len(parts) != 2:
                print(f"Skipping {file_path.name}: Invalid filename format")
                continue
            api = parts[0]
            try:
                qps = int(parts[1])
            except ValueError:
                print(f"Skipping {file_path.name}: Invalid QPS value")
                continue
            if qps_filter and qps not in qps_filter:
                continue
        else:
            print(f"Skipping {file_path.name}: Invalid filename format")
            continue

        # Analyze
        label = "serial" if qps == "serial" else f"{qps} QPS"
        print(f"Analyzing {api} @ {label}...")
        analysis = analyze_jsonl(str(file_path))

        if analysis:
            all_results[api][qps] = analysis
        else:
            print(f"  Warning: No data in {file_path.name}")

    return dict(all_results)


def generate_summary_text(all_results: Dict[str, Dict[str, Any]]) -> str:
    """Generate human-readable summary report."""
    lines = []

    lines.append("=" * 100)
    lines.append("MULTI-API PERFORMANCE TEST RESULTS")
    lines.append("=" * 100)
    lines.append("")

    def api_sort_key(a):
        return (0, a) if a == "octen" else (1, a)

    # Per-API reports
    for api in sorted(all_results.keys(), key=api_sort_key):
        lines.append("")
        lines.append("=" * 100)
        lines.append(f"API: {api.upper()}")
        lines.append("=" * 100)
        lines.append("")

        # Table header
        lines.append(f"{'QPS':<6} | {'Requests':<9} | {'Success%':<8} | {'P50(ms)':<8} | {'P90(ms)':<8} | {'P99(ms)':<8} | {'Errors by Type':<50}")
        lines.append("-" * 100)

        # Sort by QPS (serial sorts last)
        def qps_sort_key(q):
            return (1, q) if isinstance(q, int) else (2, q)

        for qps in sorted(all_results[api].keys(), key=qps_sort_key):
            analysis = all_results[api][qps]

            # Format error types
            error_types_str = ", ".join(
                f"{et.replace('_', ' ').title()}: {count} ({count/analysis['total_requests']*100:.1f}%)"
                for et, count in sorted(analysis['error_by_type'].items())
            )
            if not error_types_str:
                error_types_str = "None"

            # Get percentiles (use total_time)
            p = analysis['total_time_percentiles']

            qps_label = str(qps)
            lines.append(
                f"{qps_label:<6} | {analysis['total_requests']:<9,} | {analysis['success_rate']:<8.2f} | "
                f"{p['p50']:<8.0f} | {p['p90']:<8.0f} | {p['p99']:<8.0f} | "
                f"{error_types_str[:50]}"
            )

        # Error summary for this API
        lines.append("")
        lines.append(f"Error Summary for {api.upper()}:")

        # Aggregate errors across all QPS levels
        total_requests_all = sum(analysis['total_requests'] for analysis in all_results[api].values())
        total_errors_by_type = defaultdict(int)

        for analysis in all_results[api].values():
            for error_type, count in analysis['error_by_type'].items():
                total_errors_by_type[error_type] += count

        if total_errors_by_type:
            for error_type in sorted(total_errors_by_type.keys()):
                count = total_errors_by_type[error_type]
                pct = (count / total_requests_all * 100) if total_requests_all > 0 else 0
                lines.append(f"  {error_type.replace('_', ' ').title():<20}: {count:>6,} requests ({pct:.2f}%)")
        else:
            lines.append("  No errors")

        lines.append("")

    # Cross-API comparison at each QPS level
    lines.append("")
    lines.append("=" * 100)
    lines.append("CROSS-API COMPARISON BY QPS LEVEL")
    lines.append("=" * 100)

    # Find all QPS levels tested
    all_qps = sorted(set(
        qps
        for api_results in all_results.values()
        for qps in api_results.keys()
    ))

    for qps in all_qps:
        lines.append("")
        lines.append(f"QPS Level: {qps}")
        lines.append("-" * 100)
        lines.append(f"{'API':<15} | {'Success%':<8} | {'P50(ms)':<8} | {'P90(ms)':<8} | {'P99(ms)':<8} | {'Error Rate%':<12} | {'Actual QPS':<10}")
        lines.append("-" * 100)

        # Collect data for this QPS level
        qps_data = []
        for api in sorted(all_results.keys()):
            if qps in all_results[api]:
                analysis = all_results[api][qps]
                p = analysis['total_time_percentiles']
                qps_data.append({
                    "api": api,
                    "success_rate": analysis['success_rate'],
                    "p50": p['p50'],
                    "p90": p['p90'],
                    "p99": p['p99'],
                    "error_rate": analysis['error_rate'],
                    "actual_qps": analysis['actual_qps'],
                })

        # Sort by P50 latency (ascending)
        qps_data.sort(key=lambda x: x['p50'])

        for data in qps_data:
            lines.append(
                f"{data['api']:<15} | {data['success_rate']:<8.2f} | "
                f"{data['p50']:<8.0f} | {data['p90']:<8.0f} | {data['p99']:<8.0f} | "
                f"{data['error_rate']:<12.2f} | {data['actual_qps']:<10.2f}"
            )

        # Identify best performer
        if qps_data:
            best_success = max(qps_data, key=lambda x: x['success_rate'])
            best_latency = qps_data[0]  # already sorted by p50 ascending
            lines.append("")
            lines.append(f"  🏆 Best Success Rate: {best_success['api']} ({best_success['success_rate']:.2f}%)")
            lines.append(f"  ⚡ Best Latency (P50): {best_latency['api']} ({best_latency['p50']:.0f}ms)")

    lines.append("")
    lines.append("=" * 100)

    return "\n".join(lines)


def generate_latency_csv(all_results: Dict[str, Dict[str, Any]]) -> List[List[str]]:
    """Generate CSV data for latency comparison."""
    # CSV header
    csv_data = [
        ["API", "QPS", "Total Requests", "Success Rate %", "Error Rate %",
         "P50 (ms)", "P90 (ms)", "P99 (ms)", "Actual QPS",
         "Timeout Errors", "Rate Limit Errors", "Connection Errors", "API Errors"]
    ]

    def qps_sort_key(q):
        return (1, q) if isinstance(q, int) else (2, q)

    def api_sort_key(a):
        return (0, a) if a == "octen" else (1, a)

    # Add rows
    for api in sorted(all_results.keys(), key=api_sort_key):
        for qps in sorted(all_results[api].keys(), key=qps_sort_key):
            analysis = all_results[api][qps]
            p = analysis['total_time_percentiles']

            # Get error counts by type
            errors = analysis['error_by_type']

            row = [
                api,
                str(qps),
                str(analysis['total_requests']),
                f"{analysis['success_rate']:.2f}",
                f"{analysis['error_rate']:.2f}",
                f"{p['p50']:.0f}",
                f"{p['p90']:.0f}",
                f"{p['p99']:.0f}",
                f"{analysis['actual_qps']:.2f}",
                str(errors.get('timeout', 0)),
                str(errors.get('rate_limit', 0)),
                str(errors.get('connection_error', 0)),
                str(errors.get('api_error', 0)),
            ]
            csv_data.append(row)

    return csv_data


def save_results(all_results: Dict[str, Dict[str, Any]], results_dir: str):
    """Save analysis results in multiple formats."""
    results_path = Path(results_dir)

    # 1. Save JSON (machine-readable)
    json_file = results_path / "summary.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Saved JSON summary: {json_file}")

    # 2. Save text report (human-readable)
    txt_file = results_path / "summary.txt"
    summary_text = generate_summary_text(all_results)
    with open(txt_file, 'w', encoding='utf-8') as f:
        f.write(summary_text)
    print(f"✅ Saved text summary: {txt_file}")

    # 3. Save CSV (spreadsheet import)
    csv_file = results_path / "latency_comparison.csv"
    csv_data = generate_latency_csv(all_results)
    with open(csv_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(csv_data)
    print(f"✅ Saved CSV comparison: {csv_file}")


def print_summary_stats(all_results: Dict[str, Dict[str, Any]]):
    """Print quick summary statistics."""
    total_apis = len(all_results)
    total_tests = sum(len(api_results) for api_results in all_results.values())
    total_requests = sum(
        analysis['total_requests']
        for api_results in all_results.values()
        for analysis in api_results.values()
    )

    print("\n" + "=" * 80)
    print("ANALYSIS SUMMARY")
    print("=" * 80)
    print(f"APIs analyzed: {total_apis}")
    print(f"Test combinations: {total_tests}")
    print(f"Total requests processed: {total_requests:,}")
    print("=" * 80)


def main():
    """Main execution function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Multi-API Performance Test Results Analyzer"
    )
    parser.add_argument(
        "--results-dir",
        default=RESULTS_DIR,
        help=f"Results directory (default: {RESULTS_DIR})"
    )
    parser.add_argument(
        "--mode",
        choices=["serial", "qps"],
        default=None,
        help="Filter result type: 'serial' for serial tests, 'qps' for QPS load tests (default: all)"
    )
    parser.add_argument(
        "--qps",
        nargs="+",
        type=int,
        default=None,
        help="When --mode qps: only analyze these QPS levels, e.g. --qps 1 10 50"
    )

    args = parser.parse_args()

    print("=" * 80)
    print("Multi-API Performance Test Results Analyzer")
    print("=" * 80)

    # Load and analyze all results
    all_results = load_all_results(args.results_dir, mode=args.mode, qps_filter=args.qps)

    if not all_results:
        print("\n❌ No results found!")
        return

    # Print summary stats
    print_summary_stats(all_results)

    # Save results
    save_results(all_results, args.results_dir)

    # Print text summary to console
    print("\n" + "=" * 80)
    print("RESULTS PREVIEW")
    print("=" * 80)
    summary_text = generate_summary_text(all_results)
    print(summary_text)

    print("\n" + "=" * 80)
    print("Analysis complete! Check the results directory for detailed reports.")
    print("=" * 80)


if __name__ == "__main__":
    main()
