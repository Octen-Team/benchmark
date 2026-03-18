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
import time
import asyncio
import signal
from pathlib import Path
from typing import List, Dict, Optional

from enhanced_api_client import EnhancedAPIClient, API_CONFIGS


# Default test configuration
DEFAULT_APIS = ["exa", "tavily", "brave", "perplexity", "octen"]
DEFAULT_QPS_LEVELS = [1, 5, 10, 15, 20, 50]
QUERIES_FILE = "queries_10k.txt"
RESULTS_DIR = "results"


class TestOrchestrator:
    """Orchestrates multi-API performance testing."""

    def __init__(self, queries: List[str], results_dir: str):
        """
        Initialize test orchestrator.

        Args:
            queries: List of query strings
            results_dir: Directory for output files
        """
        self.queries = queries
        self.results_dir = Path(results_dir)
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

    async def test_api_at_qps(
        self,
        api_name: str,
        qps: float
    ) -> Dict[str, any]:
        """
        Test a single API at a specific QPS level.

        Returns test metadata dict.
        """
        # Get queries for this QPS level (query_count = qps × 200)
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
        if Path(output_file).exists():
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
            await client.run_batch(test_queries, output_file, progress_callback)

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
        Run all test combinations (APIs × QPS levels).

        Tests run sequentially to avoid interference.
        """
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

        # Calculate total estimated time
        total_duration = 0
        for qps in qps_levels:
            query_count = min(int(qps * 120), len(self.queries))
            duration_per_api = query_count / qps
            total_duration += duration_per_api * len(apis)
        print(f"Estimated total time: {total_duration / 60:.1f} minutes ({total_duration / 3600:.1f} hours)")
        print("=" * 80)

        # Run tests sequentially
        for api in apis:
            if self.interrupted:
                print("\n⚠️  Test suite interrupted. Stopping...")
                break

            # Check if API key is available
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

                # Run test
                result = await self.test_api_at_qps(api, qps)
                self.completed_tests.append(result)
                completed += 1

                print(f"\nOverall progress: {completed}/{total_tests} tests completed")

                # Brief pause between tests
                if not self.interrupted and completed < total_tests:
                    print("  Pausing 5 seconds before next test...")
                    await asyncio.sleep(5)

        # Print summary
        self.print_summary()

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
                print(f"  • {test['api']} @ {test['qps']} QPS ({query_count} queries, {duration_min:.1f}m)")

        if failed:
            print("\nFailed tests:")
            for test in failed:
                reason = test.get("reason", "Unknown")
                print(f"  • {test['api']} @ {test['qps']} QPS - {reason}")

        if skipped:
            print("\nSkipped tests:")
            for test in skipped:
                reason = test.get("reason", "Unknown")
                print(f"  • {test['api']} @ {test['qps']} QPS - {reason}")

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

    args = parser.parse_args()

    # Load queries
    queries = load_queries(args.queries)

    if args.limit:
        queries = queries[:args.limit]
        print(f"Limited to first {args.limit} queries")

    # Create orchestrator
    orchestrator = TestOrchestrator(queries, args.results_dir)

    # Run all tests
    asyncio.run(orchestrator.run_all_tests(args.apis, args.qps_levels))


if __name__ == "__main__":
    main()
