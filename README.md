# Web Search API Performance Benchmark

Load testing framework for five web search APIs — Octen, Exa, Tavily, Brave, and Perplexity — across multiple QPS levels. Measures success rate and P50/P90/P99 latency.

---

## Directory Structure

```
benchmark/
├── README.md                    # This file
├── .env.example                 # API key template
├── .gitignore
├── enhanced_api_client.py       # API client (concurrency + rate limiting)
├── run_multi_api_tests.py       # Test orchestrator (multi-API x multi-QPS)
├── analyze_results.py           # Results analyzer (generates reports)
├── generate_query_variants.py   # Query generator (expands CSV to 10k queries)
└── sealqa_seal_hard.csv         # Base query dataset (254 queries)
```

---

## Setup

### 1. Install dependencies

```bash
pip install aiohttp
```

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env and fill in your real API keys
source .env
```

**.env format:**
```bash
export OCTEN_API_KEY="your_octen_api_key"
export EXA_API_KEY="your_exa_api_key"
export TAVILY_API_KEY="your_tavily_api_key"
export BRAVE_API_KEY="your_brave_api_key"
export PERPLEXITY_API_KEY="your_perplexity_api_key"
```

> **Note**: `.env` is listed in `.gitignore` and will never be committed to the repository.

### 3. Generate query file

Expand the 254 base queries in `sealqa_seal_hard.csv` to 10,000 unique test queries:

```bash
python3 generate_query_variants.py
# Output: queries_10k.txt
```

You can also use a pre-generated `queries_10k.txt` if one is available.

---

## Quick Start

### Run the full test suite

Tests all 5 APIs across all QPS levels (1, 5, 10, 15, 20, 50). Takes approximately 40 minutes:

```bash
source .env
python3 run_multi_api_tests.py
```

### Specify APIs and QPS levels

```bash
# Test only Octen and Perplexity at QPS 1, 10, and 50
python3 run_multi_api_tests.py --apis octen perplexity --qps-levels 1 10 50

# Test only Octen at QPS 20
python3 run_multi_api_tests.py --apis octen --qps-levels 20
```

### Analyze results

```bash
python3 analyze_results.py
```

Generates the following reports:
- `results/summary.txt` — human-readable text report
- `results/summary.json` — machine-readable JSON
- `results/latency_comparison.csv` — importable into Excel / Google Sheets

---

## CLI Reference

### `run_multi_api_tests.py`

| Argument | Default | Description |
|----------|---------|-------------|
| `--apis` | all 5 APIs | APIs to test (space-separated) |
| `--qps-levels` | `1 5 10 15 20 50` | QPS levels to test (space-separated) |
| `--queries` | `queries_10k.txt` | Path to query file |
| `--results-dir` | `results/` | Output directory for results |
| `--limit` | no limit | Use only the first N queries (for debugging) |

**Examples:**
```bash
# Quick smoke test (50 queries, QPS 1)
python3 run_multi_api_tests.py --apis octen --qps-levels 1 --limit 50

# Test high-QPS scenarios only
python3 run_multi_api_tests.py --qps-levels 20 50
```

### `analyze_results.py`

| Argument | Default | Description |
|----------|---------|-------------|
| `--results-dir` | `results/` | Directory containing JSONL result files |

---

## Result File Format

Each test combination produces one JSONL file named `{api}_qps{n}.jsonl`, e.g. `octen_qps50.jsonl`.

Each line is one request record:

```json
{
  "timestamp": "2026-03-17T12:30:00.000Z",
  "api": "octen",
  "query_id": 1,
  "query": "What is the capital of France?",
  "status": 200,
  "total_time": 0.064,
  "api_time": 0.059,
  "error": null,
  "error_type": null
}
```

| Field | Description |
|-------|-------------|
| `status` | HTTP status code; 200 = success |
| `total_time` | Client-side total latency (seconds) |
| `api_time` | Server-side processing time (seconds); only available for Tavily and Octen |
| `error_type` | One of: `rate_limit` / `timeout` / `connection_error` / `api_error` |

---

## Query Allocation Strategy

Each QPS level uses a **non-overlapping** slice of the query pool to avoid cache effects:

| QPS | Query Range | Count | Duration |
|-----|-------------|-------|----------|
| 1 | [0, 120) | 120 | ~120 s |
| 5 | [120, 720) | 600 | ~120 s |
| 10 | [720, 1920) | 1,200 | ~120 s |
| 15 | [1920, 3720) | 1,800 | ~120 s |
| 20 | [3720, 6120) | 2,400 | ~120 s |
| 50 | [6120, 10000) | 3,880 | ~78 s |

---

## Concurrency Architecture

```
AsyncRateLimiter (precise QPS control)
       |
Semaphore (max_concurrency = max(10, QPS x 2))
       |
Concurrent requests -> aiohttp ClientSession (connection pool)
       |
Streaming JSONL writes (ordered by query_id)
```

- **QPS accuracy**: measured deviation < 4% across all test runs
- **Latency measurement**: only successful requests (HTTP 200) are included in latency stats

---

## Supported APIs

| API | Environment Variable | Request Method | Server-side Timing |
|-----|---------------------|----------------|-------------------|
| Octen | `OCTEN_API_KEY` | POST | Yes (`meta.latency`, milliseconds) |
| Exa | `EXA_API_KEY` | POST | No |
| Tavily | `TAVILY_API_KEY` | POST | Yes (`response_time`, seconds) |
| Brave | `BRAVE_API_KEY` | GET | No |
| Perplexity | `PERPLEXITY_API_KEY` | POST | No |

---

## Benchmark Results (2026-03-17)

| API | Safe QPS | P50 @ 10 QPS | P50 @ 20 QPS | P50 @ 50 QPS | Notes |
|-----|----------|-------------|-------------|-------------|-------|
| **Octen** | **>=50** | 70ms | 69ms | 64ms | Latency improves at higher QPS |
| **Perplexity** | **~50** | 576ms | 554ms | 512ms | Stable under high load |
| **Tavily** | **~20** | 168ms | 166ms | 371ms ❌ | Rate limited above 20 QPS |
| **Brave** | **~15** | 522ms | 511ms | 539ms ❌ | Rate limited above 20 QPS |
| **Exa** | **~5** | 452ms | 448ms ❌ | 430ms ❌ | Heavily rate limited above 10 QPS |
