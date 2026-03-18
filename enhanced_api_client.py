#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Enhanced API Client Wrapper for Multi-API Performance Testing

Wraps existing API clients (Exa, Tavily, Brave, Perplexity, Octen) with:
- Precise QPS control
- Request timing (total_time + api_time)
- Comprehensive error handling and categorization
- JSONL streaming output per request
"""

import os
import sys
import json
import time
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import ClientConnectorError, ClientSSLError


# API endpoints and default settings
API_CONFIGS = {
    "exa": {
        "url": "https://api.exa.ai/search",
        "env_key": "EXA_API_KEY",
        "header_key": "x-api-key",
        "default_qpm": 240,  # 4 QPS
        "timeout": 60,
    },
    "tavily": {
        "url": "https://api.tavily.com/search",
        "env_key": "TAVILY_API_KEY",
        "header_key": "Authorization",
        "header_prefix": "Bearer ",
        "default_qpm": 100,  # 1.67 QPS
        "timeout": 60,
    },
    "brave": {
        "url": "https://api.search.brave.com/res/v1/web/search",
        "env_key": "BRAVE_API_KEY",
        "header_key": "X-Subscription-Token",
        "default_qpm": 1200,  # 20 QPS
        "timeout": 45,
    },
    "perplexity": {
        "url": "https://api.perplexity.ai/search",
        "env_key": "PERPLEXITY_API_KEY",
        "header_key": "Authorization",
        "header_prefix": "Bearer ",
        "default_qpm": 2400,  # 40 QPS
        "timeout": 90,
    },
    "octen": {
        "url": "https://api.octen.ai/search",
        "env_key": "OCTEN_API_KEY",
        "header_key": "x-api-key",
        "default_qpm": 1200,  # 20 QPS
        "timeout": 90,
    },
}


class AsyncRateLimiter:
    """Precise rate limiter by QPS (queries per second)."""

    def __init__(self, qps: float):
        if qps <= 0:
            raise ValueError("QPS must be > 0")
        self.min_interval = 1.0 / float(qps)
        self._lock = asyncio.Lock()
        self._next_time = time.perf_counter()

    async def acquire(self):
        """Wait until it's time for the next request."""
        async with self._lock:
            now = time.perf_counter()
            if now < self._next_time:
                await asyncio.sleep(self._next_time - now)
            self._next_time = max(self._next_time, time.perf_counter()) + self.min_interval


class EnhancedAPIClient:
    """
    Unified API client wrapper with precise QPS control and metrics.

    Supports: Exa, Tavily, Brave, Perplexity, Octen
    """

    def __init__(self, api_name: str, qps: float, api_key: Optional[str] = None):
        """
        Initialize enhanced API client.

        Args:
            api_name: One of: exa, tavily, brave, perplexity, octen
            qps: Target queries per second
            api_key: API key (if None, read from environment)
        """
        if api_name not in API_CONFIGS:
            raise ValueError(f"Unknown API: {api_name}. Choose from: {list(API_CONFIGS.keys())}")

        self.api_name = api_name
        self.config = API_CONFIGS[api_name]
        self.qps = qps
        self.limiter = AsyncRateLimiter(qps)

        # Get API key
        if api_key is None:
            api_key = os.getenv(self.config["env_key"], "").strip()
            if not api_key:
                raise ValueError(f"Missing API key. Set env var: {self.config['env_key']}")
        self.api_key = api_key

        # Request counter
        self.request_count = 0
        self.error_count = 0

    def _build_headers(self) -> Dict[str, str]:
        """Build HTTP headers for API request."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Add API key to appropriate header
        header_key = self.config["header_key"]
        prefix = self.config.get("header_prefix", "")
        headers[header_key] = f"{prefix}{self.api_key}"

        return headers

    def _build_payload(self, query: str) -> Dict[str, Any]:
        """Build request payload for each API."""
        # Validate and truncate query if needed
        if self.api_name == "brave":
            # Brave has strict limits: max 400 chars, max 50 words
            if len(query) > 400:
                query = query[:397] + "..."
            words = query.split()
            if len(words) > 50:
                query = ' '.join(words[:50])

        elif self.api_name == "octen":
            # Octen: max 490 chars
            if len(query) > 490:
                query = query[:487] + "..."

        elif self.api_name == "tavily":
            # Tavily: max 400 chars (safe limit)
            if len(query) > 400:
                query = query[:400]

        # Build API-specific payload
        if self.api_name == "exa":
            return {
                "query": query,
                "numResults": 5,
                "type": "instant",
                "contents": {
                    "highlights": {
                        "maxCharacters": 2048
                    }
                }
            }

        elif self.api_name == "tavily":
            return {
                "query": query,
                "search_depth": "ultra-fast"
            }

        elif self.api_name == "brave":
            return {
                "q": query,
                "count": 5
            }

        elif self.api_name == "perplexity":
            return {
                "query": query,
                "search_domain_filter": [],
                "return_citations": True,
                "return_images": False,
                "recency_filter": "month"
            }

        elif self.api_name == "octen":
            return {
                "query": query,
                "highlight_number": 3,
                "sentence_per_highlight": 3
            }
        else:
            # Defensive programming - should never reach here due to validation in __init__
            raise ValueError(f"Unsupported API: {self.api_name}")

    def _categorize_error(self, status_code: Optional[int], error_msg: str) -> str:
        """Categorize error type from status code and error message."""
        if status_code == 429:
            return "rate_limit"
        elif status_code in (400, 422):
            return "validation_error"
        elif status_code in (502, 503, 504):
            return "connection_error"
        elif status_code in (500, 501):
            return "api_error"
        elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            return "timeout"
        elif "connection" in error_msg.lower() or "ssl" in error_msg.lower():
            return "connection_error"
        else:
            return "api_error"

    async def execute_query(
        self,
        session: aiohttp.ClientSession,
        query: str,
        query_id: int
    ) -> Dict[str, Any]:
        """
        Execute a single query with timing and error handling.

        Returns JSONL record:
        {
            "timestamp": "...",
            "api": "exa",
            "query_id": 1,
            "query": "...",
            "status": 200,
            "total_time": 0.523,
            "api_time": 0.456,
            "error": null,
            "error_type": null
        }
        """
        # Initialize variables at the start to prevent NameError
        total_time = 0.0
        status_code = None
        api_time = None
        error_msg = None
        error_type = None

        # Wait for rate limiter
        await self.limiter.acquire()

        try:
            # Prepare request
            headers = self._build_headers()
            payload = self._build_payload(query)
            url = self.config["url"]
            timeout_sec = self.config["timeout"]

            # Execute request with timing
            t0 = time.perf_counter()

            # For Brave, use GET with params instead of POST
            if self.api_name == "brave":
                async with session.get(
                    url,
                    headers=headers,
                    params=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout_sec),
                ) as resp:
                    text = await resp.text()
                    total_time = time.perf_counter() - t0
                    status_code = resp.status

                    if resp.status == 200:
                        # Success
                        data = json.loads(text)
                        # Brave doesn't return api_time
                        api_time = None
                        self.request_count += 1
                    else:
                        # Error
                        error_msg = f"HTTP {resp.status}: {text[:300]}"
                        error_type = self._categorize_error(resp.status, error_msg)
                        self.error_count += 1
            else:
                # POST request for other APIs
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout_sec),
                ) as resp:
                    text = await resp.text()
                    total_time = time.perf_counter() - t0
                    status_code = resp.status

                    if resp.status == 200:
                        # Success - try to extract API time
                        data = json.loads(text)

                        # Extract API time if available
                        if self.api_name == "tavily":
                            try:
                                api_time = float(data.get("response_time", 0)) or None
                            except (TypeError, ValueError):
                                api_time = None
                        elif self.api_name == "octen":
                            try:
                                # Octen returns latency in milliseconds in meta.latency
                                latency_ms = data.get("meta", {}).get("latency", 0)
                                api_time = float(latency_ms) / 1000.0 if latency_ms else None
                            except (TypeError, ValueError):
                                api_time = None
                        else:
                            # Other APIs don't provide server-side timing
                            api_time = None

                        self.request_count += 1
                    else:
                        # Error
                        error_msg = f"HTTP {resp.status}: {text[:300]}"
                        error_type = self._categorize_error(resp.status, error_msg)
                        self.error_count += 1

        except asyncio.TimeoutError:
            if 't0' in locals():
                total_time = time.perf_counter() - t0
            error_msg = f"Timeout after {timeout_sec if 'timeout_sec' in locals() else 60}s"
            error_type = "timeout"
            self.error_count += 1

        except (ClientConnectorError, ClientSSLError) as e:
            if 't0' in locals():
                total_time = time.perf_counter() - t0
            error_msg = f"Connection error: {repr(e)}"
            error_type = "connection_error"
            self.error_count += 1

        except Exception as e:
            if 't0' in locals():
                total_time = time.perf_counter() - t0
            error_msg = f"Exception: {repr(e)}"
            error_type = "api_error"
            self.error_count += 1

        # Build JSONL record
        record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "api": self.api_name,
            "query_id": query_id,
            "query": query,
            "status": status_code,
            "total_time": round(total_time, 3) if total_time else None,
            "api_time": round(api_time, 3) if api_time else None,
            "error": error_msg,
            "error_type": error_type,
        }

        return record

    async def run_batch(
        self,
        queries: List[str],
        output_file: str,
        progress_callback: Optional[callable] = None,
        max_concurrency: Optional[int] = None
    ) -> None:
        """
        Run batch of queries with streaming JSONL output and concurrency control.

        Args:
            queries: List of query strings
            output_file: Path to JSONL output file
            progress_callback: Optional callback function(completed, total)
            max_concurrency: Maximum concurrent requests (default: auto-calculated from QPS)
        """
        total = len(queries)
        completed = 0

        # Auto-calculate max concurrency if not specified
        # Rule: max_concurrency = max(10, QPS * 2)
        # This allows 2 seconds worth of requests to be in-flight
        if max_concurrency is None:
            max_concurrency = max(10, int(self.qps * 2))

        # Create output file
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Semaphore to control concurrency
        semaphore = asyncio.Semaphore(max_concurrency)

        # Results storage (keyed by query_id to maintain order)
        results = {}
        results_lock = asyncio.Lock()

        # File write lock
        file_lock = asyncio.Lock()

        async def execute_with_semaphore(session, query: str, query_id: int):
            """Execute query with concurrency control."""
            async with semaphore:
                # Rate limiter controls request start time (QPS control)
                record = await self.execute_query(session, query, query_id)

                # Store result
                async with results_lock:
                    results[query_id] = record

                return record

        # Open JSONL file for streaming writes
        with open(output_file, 'w', encoding='utf-8') as f:
            # Create HTTP session
            connector = aiohttp.TCPConnector(limit=max_concurrency * 2)
            async with aiohttp.ClientSession(connector=connector) as session:

                # Track in-flight tasks
                tasks = []
                next_write_id = 1  # Next query_id to write to file (maintains order)

                # Create all tasks (but limited by semaphore)
                for i, query in enumerate(queries):
                    query_id = i + 1
                    task = asyncio.create_task(execute_with_semaphore(session, query, query_id))
                    tasks.append(task)

                # Process completed tasks and write results in order
                for task in asyncio.as_completed(tasks):
                    await task

                    # Write all consecutive completed results
                    async with results_lock:
                        while next_write_id in results:
                            record = results[next_write_id]
                            async with file_lock:
                                f.write(json.dumps(record, ensure_ascii=False) + '\n')
                                f.flush()

                            # Update progress
                            completed += 1
                            if progress_callback:
                                progress_callback(completed, total)

                            del results[next_write_id]
                            next_write_id += 1

        # Final statistics
        success_rate = (self.request_count / total * 100) if total > 0 else 0
        print(f"\n{self.api_name} batch complete:")
        print(f"  Successful: {self.request_count}/{total} ({success_rate:.1f}%)")
        print(f"  Errors: {self.error_count}/{total}")


async def test_api_at_qps(
    api_name: str,
    qps: float,
    queries: List[str],
    output_file: str
) -> None:
    """
    Test a single API at a specific QPS level.

    Args:
        api_name: API to test (exa, tavily, brave, perplexity, octen)
        qps: Target queries per second
        queries: List of queries to execute
        output_file: Path to output JSONL file
    """
    print(f"\nTesting {api_name} at {qps} QPS...")
    print(f"Total queries: {len(queries)}")
    print(f"Estimated duration: {len(queries) / qps / 60:.1f} minutes")

    # Create client
    client = EnhancedAPIClient(api_name, qps)

    # Progress callback
    start_time = time.time()

    def progress_callback(completed, total):
        if completed % 100 == 0 or completed == total:
            elapsed = time.time() - start_time
            qps_actual = completed / elapsed if elapsed > 0 else 0
            print(f"  Progress: {completed}/{total} ({completed/total*100:.1f}%) - "
                  f"Actual QPS: {qps_actual:.2f}")

    # Run batch
    await client.run_batch(queries, output_file, progress_callback)


def main():
    """Test the enhanced API client."""
    import argparse

    parser = argparse.ArgumentParser(description="Test Enhanced API Client")
    parser.add_argument("--api", required=True, choices=list(API_CONFIGS.keys()),
                        help="API to test")
    parser.add_argument("--qps", type=float, required=True,
                        help="Target queries per second")
    parser.add_argument("--queries", required=True,
                        help="Path to queries file (one per line)")
    parser.add_argument("--output", required=True,
                        help="Path to output JSONL file")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of queries (for testing)")

    args = parser.parse_args()

    # Load queries
    with open(args.queries, 'r', encoding='utf-8') as f:
        queries = [line.strip() for line in f if line.strip()]

    if args.limit:
        queries = queries[:args.limit]

    print(f"Loaded {len(queries)} queries")

    # Run test
    asyncio.run(test_api_at_qps(
        args.api,
        args.qps,
        queries,
        args.output
    ))


if __name__ == "__main__":
    main()
