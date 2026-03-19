#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Multi-API Performance Test Orchestrator

Runs performance tests for multiple APIs (Exa, Tavily, Brave, Perplexity, Octen)
at multiple QPS levels (1, 5, 10, 15, 20, 50) using 10,000 queries.

Tests run sequentially to avoid cross-interference.
Results saved to: results/{api}_qps{qps}.jsonl
"""

import os
import sys
import csv
import time
import asyncio
import signal
from pathlib import Path
from typing import List, Dict

from enhanced_api_client import EnhancedAPIClient, API_CONFIGS


# Default test configuration
DEFAULT_APIS = ["exa", "tavily", "brave", "perplexity", "octen"]
DEFAULT_QPS_LEVELS = [1, 5, 10, 15, 20, 50]
QUERIES_FILE = "queries_10k.txt"
CSV_QUERIES_FILE = "sealqa_seal_hard.csv"
RESULTS_DIR = "results"


class TestOrchestrator:
    """Orchestrates multi-API performance testing."""

    def __init__(self, queries: List[str], results_dir: str, serial: bool = False):
        """
        Initialize test orchestrator.

        Args:
            queries: List of query strings
            results_dir: Directory for output files
            serial: If True, run with max_concurrency=1 (serial execution)
        """
        self.queries = queries
        self.results_dir = Path(results_dir)
        self.serial = serial
        self.force = False
        self.max_concurrency = 1 if serial else None
        self.results_dir.mkdir(parents=True, exist_ok=True)

        # Track completed tests for graceful interruption
        self.completed_tests: List[Dict[str, any]] = []
        self.interrupted = False

        # Setup signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle interruption signals gracefully."""
        print("\n\n⚠️  Interruption detected! Finishing current test and saving results...")
        self.interrupted = True

    def get_output_file(self, api: str, qps: float) -> str:
        """Get output file path for API and QPS level."""
        filename = f"{api}_qps{int(qps)}.jsonl"
        return str(self.results_dir / filename)

    def get_queries_for_qps(self, qps: float) -> List[str]:
        """
        Get query subset for specific QPS level.

        Query count = QPS × 120 (ensures ~120 seconds test duration)
        Each QPS level uses DIFFERENT non-overlapping queries to avoid reuse.

        Query allocation (non-overlapping ranges):
        - QPS 1:  queries[0:120]         (120 queries)
        - QPS 5:  queries[120:720]       (600 queries)
        - QPS 10: queries[720:1920]      (1200 queries)
        - QPS 15: queries[1920:3720]     (1800 queries)
        - QPS 20: queries[3720:6120]     (2400 queries)
        - QPS 50: queries[6120:10000]    (3880 queries, ~77.6s test)

        Args:
            qps: Target queries per second

        Returns:
            List of queries for this QPS level
        """
        # Define non-overlapping query ranges for each QPS level
        qps_ranges = {
            1: (0, 120),          # 120 queries
            5: (120, 720),        # 600 queries
            10: (720, 1920),      # 1200 queries
            15: (1920, 3720),     # 1800 queries
            20: (3720, 6120),     # 2400 queries
            50: (6120, 10000),    # 3880 queries (fits in remaining space)
        }

        # Get the range for this QPS level
        if qps in qps_ranges:
            start, end = qps_ranges[qps]
            return self.queries[start:end]
        else:
            # Fallback for other QPS levels (use old behavior)
            query_count = int(qps * 120)
            query_count = min(query_count, len(self.queries))
            return self.queries[:query_count]

    async def test_api_serial(self, api_name: str) -> Dict[str, any]:
        """
        Test a single API in serial mode: send all queries one by one with no rate limiting.

        Returns test metadata dict.
        """
        output_file = str(self.results_dir / f"{api_name}_serial.jsonl")
        test_queries = self.queries

        print("\n" + "=" * 80)
        print(f"Testing {api_name.upper()} (serial mode)")
        print("=" * 80)
        print(f"Queries: {len(test_queries)}")
        print(f"Concurrency: 1 (no rate limiting)")
        print(f"Output: {output_file}")

        # Check if test already completed
        if not self.force and Path(output_file).exists():
            with open(output_file, 'r') as f:
                existing_count = sum(1 for _ in f)
            if existing_count == len(test_queries):
                print(f"⚠️  Test already completed ({existing_count} queries). Skipping...")
                return {
                    "api": api_name,
                    "mode": "serial",
                    "query_count": len(test_queries),
                    "status": "skipped",
                    "reason": "already_completed",
                    "output_file": output_file
                }

        # Create client with a very high QPS so the rate limiter is effectively a no-op
        try:
            client = EnhancedAPIClient(api_name, qps=9999)
        except ValueError as e:
            print(f"❌ Failed to initialize {api_name}: {e}")
            return {
                "api": api_name,
                "mode": "serial",
                "status": "failed",
                "reason": str(e),
                "output_file": output_file
            }

        # Progress tracking
        start_time = time.time()

        def progress_callback(completed, total):
            if completed % 50 == 0 or completed == total:
                elapsed = time.time() - start_time
                pct = completed / total * 100
                avg_latency = elapsed / completed * 1000 if completed > 0 else 0
                print(f"  Progress: {completed:,}/{total:,} ({pct:.1f}%) | "
                      f"Avg latency: {avg_latency:.0f}ms")

        try:
            await client.run_batch(test_queries, output_file, progress_callback,
                                   max_concurrency=1)

            duration = time.time() - start_time

            # Calculate P50/P90/P99 from the output file
            import json as _json
            latencies = []
            with open(output_file, 'r') as f:
                for line in f:
                    rec = _json.loads(line)
                    if rec.get("status") == 200 and rec.get("total_time"):
                        latencies.append(rec["total_time"] * 1000)  # convert to ms

            def _pct(values, p):
                if not values:
                    return 0
                s = sorted(values)
                k = (len(s) - 1) * p / 100
                lo, hi = int(k), min(int(k) + 1, len(s) - 1)
                return s[lo] + (s[hi] - s[lo]) * (k - lo)

            p50 = _pct(latencies, 50)
            p90 = _pct(latencies, 90)
            p99 = _pct(latencies, 99)

            print(f"\n✅ Completed {api_name} (serial)")
            print(f"   Duration: {duration:.0f} seconds ({duration / 60:.1f} minutes)")
            print(f"   Queries: {len(test_queries)} total, {len(latencies)} successful")
            print(f"   P50: {p50:.0f}ms | P90: {p90:.0f}ms | P99: {p99:.0f}ms")

            return {
                "api": api_name,
                "mode": "serial",
                "query_count": len(test_queries),
                "status": "completed",
                "duration": duration,
                "output_file": output_file
            }

        except Exception as e:
            print(f"❌ Error running {api_name} (serial): {e}")
            return {
                "api": api_name,
                "mode": "serial",
                "query_count": len(test_queries),
                "status": "failed",
                "reason": str(e),
                "output_file": output_file
            }

    async def test_api_at_qps(
        self,
        api_name: str,
        qps: float
    ) -> Dict[str, any]:
        """
        Test a single API at a specific QPS level.

        Returns test metadata dict.
        """
        test_queries = self.get_queries_for_qps(qps)
        output_file = self.get_output_file(api_name, qps)

        print("\n" + "=" * 80)
        print(f"Testing {api_name.upper()} at {qps} QPS")
        print("=" * 80)
        print(f"Queries for this QPS level: {len(test_queries)} (QPS × 120)")
        estimated_duration = len(test_queries) / qps
        print(f"Estimated duration: {estimated_duration:.0f} seconds ({estimated_duration / 60:.1f} minutes)")
        print(f"Output: {output_file}")

        # Check if test already completed
        if not self.force and Path(output_file).exists():
            with open(output_file, 'r') as f:
                existing_count = sum(1 for _ in f)
            if existing_count == len(test_queries):
                print(f"⚠️  Test already completed ({existing_count} queries). Skipping...")
                return {
                    "api": api_name,
                    "qps": qps,
                    "query_count": len(test_queries),
                    "status": "skipped",
                    "reason": "already_completed",
                    "output_file": output_file
                }

        # Create client
        try:
            client = EnhancedAPIClient(api_name, qps)
        except ValueError as e:
            print(f"❌ Failed to initialize {api_name}: {e}")
            return {
                "api": api_name,
                "qps": qps,
                "status": "failed",
                "reason": str(e),
                "output_file": output_file
            }

        # Progress tracking
        start_time = time.time()
        last_update = start_time

        def progress_callback(completed, total):
            nonlocal last_update
            now = time.time()

            # Update every 50 queries or on completion (more frequent for smaller batches)
            if completed % 50 == 0 or completed == total:
                elapsed = now - start_time
                qps_actual = completed / elapsed if elapsed > 0 else 0
                pct = completed / total * 100
                eta_seconds = (total - completed) / qps_actual if qps_actual > 0 else 0

                print(f"  Progress: {completed:,}/{total:,} ({pct:.1f}%) | "
                      f"Actual QPS: {qps_actual:.2f} | "
                      f"ETA: {eta_seconds / 60:.1f}m")
                last_update = now

        # Run batch with queries for this QPS level
        try:
            await client.run_batch(test_queries, output_file, progress_callback,
                                   max_concurrency=self.max_concurrency)

            end_time = time.time()
            duration = end_time - start_time

            print(f"\n✅ Completed {api_name} at {qps} QPS")
            print(f"   Duration: {duration / 60:.1f} minutes ({duration:.0f} seconds)")
            print(f"   Actual QPS: {len(test_queries) / duration:.2f}")

            return {
                "api": api_name,
                "qps": qps,
                "query_count": len(test_queries),
                "status": "completed",
                "duration": duration,
                "output_file": output_file
            }

        except Exception as e:
            print(f"❌ Error running {api_name} at {qps} QPS: {e}")
            return {
                "api": api_name,
                "qps": qps,
                "query_count": len(test_queries),
                "status": "failed",
                "reason": str(e),
                "output_file": output_file
            }

    async def run_all_tests(
        self,
        apis: List[str],
        qps_levels: List[float]
    ) -> None:
        """
        Run all test combinations (APIs × QPS levels), or serial tests if self.serial is set.

        Tests run sequentially to avoid interference.
        """
        if self.serial:
            await self._run_serial_tests(apis)
        else:
            await self._run_qps_tests(apis, qps_levels)

        self.print_summary()

    async def _run_serial_tests(self, apis: List[str]) -> None:
        """Run serial latency tests for each API (no QPS control)."""
        print("\n" + "=" * 80)
        print("SERIAL LATENCY TEST")
        print("=" * 80)
        print(f"APIs: {', '.join(apis)}")
        print(f"Queries: {len(self.queries)} (from CSV, single concurrency)")
        print(f"Results directory: {self.results_dir}")
        print("=" * 80)

        for i, api in enumerate(apis):
            if self.interrupted:
                print("\n⚠️  Test suite interrupted. Stopping...")
                break

            # Check if API key is available
            config = API_CONFIGS.get(api)
            if config:
                api_key = os.getenv(config["env_key"], "").strip()
                if not api_key:
                    print(f"\n⚠️  Skipping {api}: Missing API key ({config['env_key']})")
                    continue

            result = await self.test_api_serial(api)
            self.completed_tests.append(result)

            if not self.interrupted and i < len(apis) - 1:
                print("  Pausing 5 seconds before next test...")
                await asyncio.sleep(5)

    async def _run_qps_tests(self, apis: List[str], qps_levels: List[float]) -> None:
        """Run QPS-based load tests for all API × QPS combinations."""
        total_tests = len(apis) * len(qps_levels)
        completed = 0

        print("\n" + "=" * 80)
        print("MULTI-API PERFORMANCE TEST SUITE")
        print("=" * 80)
        print(f"APIs: {', '.join(apis)}")
        print(f"QPS Levels: {', '.join(str(q) for q in qps_levels)}")
        print(f"Total test combinations: {total_tests}")
        print(f"Query allocation (QPS × 120):")
        for qps in qps_levels:
            query_count = min(int(qps * 120), len(self.queries))
            duration_sec = query_count / qps
            print(f"  QPS {qps:2.0f}: {query_count:5,} queries (~{duration_sec:.0f}s per API)")
        print(f"Total queries available: {len(self.queries):,}")
        print(f"Results directory: {self.results_dir}")

        total_duration = 0
        for qps in qps_levels:
            query_count = min(int(qps * 120), len(self.queries))
            total_duration += (query_count / qps) * len(apis)
        print(f"Estimated total time: {total_duration / 60:.1f} minutes ({total_duration / 3600:.1f} hours)")
        print("=" * 80)

        for api in apis:
            if self.interrupted:
                print("\n⚠️  Test suite interrupted. Stopping...")
                break

            config = API_CONFIGS.get(api)
            if config:
                api_key = os.getenv(config["env_key"], "").strip()
                if not api_key:
                    print(f"\n⚠️  Skipping {api}: Missing API key ({config['env_key']})")
                    completed += len(qps_levels)
                    continue

            for qps in qps_levels:
                if self.interrupted:
                    print("\n⚠️  Test suite interrupted. Stopping...")
                    break

                result = await self.test_api_at_qps(api, qps)
                self.completed_tests.append(result)
                completed += 1

                print(f"\nOverall progress: {completed}/{total_tests} tests completed")

                if not self.interrupted and completed < total_tests:
                    print("  Pausing 5 seconds before next test...")
                    await asyncio.sleep(5)

    def print_summary(self):
        """Print summary of all completed tests."""
        print("\n\n" + "=" * 80)
        print("TEST SUITE SUMMARY")
        print("=" * 80)

        successful = [t for t in self.completed_tests if t["status"] == "completed"]
        failed = [t for t in self.completed_tests if t["status"] == "failed"]
        skipped = [t for t in self.completed_tests if t["status"] == "skipped"]

        print(f"Total tests: {len(self.completed_tests)}")
        print(f"  ✅ Successful: {len(successful)}")
        print(f"  ⚠️  Skipped: {len(skipped)}")
        print(f"  ❌ Failed: {len(failed)}")

        if successful:
            print("\nSuccessful tests:")
            for test in successful:
                duration_min = test.get("duration", 0) / 60
                query_count = test.get("query_count", 0)
                label = "serial" if test.get("mode") == "serial" else f"{test.get('qps')} QPS"
                print(f"  • {test['api']} @ {label} ({query_count} queries, {duration_min:.1f}m)")

        if failed:
            print("\nFailed tests:")
            for test in failed:
                reason = test.get("reason", "Unknown")
                label = "serial" if test.get("mode") == "serial" else f"{test.get('qps')} QPS"
                print(f"  • {test['api']} @ {label} - {reason}")

        if skipped:
            print("\nSkipped tests:")
            for test in skipped:
                reason = test.get("reason", "Unknown")
                label = "serial" if test.get("mode") == "serial" else f"{test.get('qps')} QPS"
                print(f"  • {test['api']} @ {label} - {reason}")

        print("=" * 80)
        print("\nNext steps:")
        print("  1. Run analysis: python3 analyze_results.py")
        print("  2. View summary: cat results/summary.txt")
        print("=" * 80)


def load_queries(queries_file: str) -> List[str]:
    """Load queries from file (one per line)."""
    queries_path = Path(queries_file)

    if not queries_path.exists():
        raise FileNotFoundError(f"Queries file not found: {queries_file}")

    with open(queries_path, 'r', encoding='utf-8') as f:
        queries = [line.strip() for line in f if line.strip()]

    print(f"Loaded {len(queries):,} queries from {queries_file}")
    return queries


def load_queries_from_csv(csv_file: str, column: str = "Query") -> List[str]:
    """Load queries from a CSV file (reads the specified column)."""
    csv_path = Path(csv_file)

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_file}")

    queries = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if column in row and row[column].strip():
                queries.append(row[column].strip())

    print(f"Loaded {len(queries):,} queries from {csv_file} (column: '{column}')")
    return queries


def main():
    """Main execution function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Multi-API Performance Test Orchestrator"
    )
    parser.add_argument(
        "--queries",
        default=QUERIES_FILE,
        help=f"Path to queries file (default: {QUERIES_FILE})"
    )
    parser.add_argument(
        "--results-dir",
        default=RESULTS_DIR,
        help=f"Results directory (default: {RESULTS_DIR})"
    )
    parser.add_argument(
        "--apis",
        nargs="+",
        choices=DEFAULT_APIS,
        default=DEFAULT_APIS,
        help="APIs to test (default: all)"
    )
    parser.add_argument(
        "--qps-levels",
        nargs="+",
        type=float,
        default=DEFAULT_QPS_LEVELS,
        help=f"QPS levels to test (default: {DEFAULT_QPS_LEVELS})"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of queries (for testing)"
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help=(
            "Serial latency test mode: single concurrency, reads queries directly from "
            f"{CSV_QUERIES_FILE} (no need to generate queries_10k.txt). "
            "Useful for measuring baseline latency per API."
        )
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run tests even if result files already exist (overwrite cached results)"
    )

    args = parser.parse_args()

    # --serial mode: load directly from CSV, no QPS control
    if args.serial:
        csv_file = args.queries if args.queries != QUERIES_FILE else CSV_QUERIES_FILE
        queries = load_queries_from_csv(csv_file)
        print(f"Serial mode: single concurrency, no rate limiting, queries from {csv_file}")
    else:
        queries = load_queries(args.queries)

    if args.limit:
        queries = queries[:args.limit]
        print(f"Limited to first {args.limit} queries")

    # Create orchestrator
    orchestrator = TestOrchestrator(queries, args.results_dir, serial=args.serial)
    orchestrator.force = args.force

    # Run all tests
    asyncio.run(orchestrator.run_all_tests(args.apis, args.qps_levels))


if __name__ == "__main__":
    main()
