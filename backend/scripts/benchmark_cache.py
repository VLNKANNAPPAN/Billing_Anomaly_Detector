r"""Compare cached and uncached customer read latency.

Run the FastAPI server first, then from ``backend`` run:
    .\.venv\Scripts\python.exe -m scripts.benchmark_cache
"""

from __future__ import annotations

import argparse
import statistics
import time
from dataclasses import dataclass

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_CUSTOMER_IDS = tuple(range(1, 11))
DEFAULT_REPEATS = 10


@dataclass
class Measurement:
    """One request's observed timing and cache header value."""

    client_time_ms: float
    server_query_time_ms: float
    cache_hit: bool


def benchmark_paths(customer_ids: tuple[int, ...]) -> list[str]:
    """Return twenty stable paths: metrics and anomalies for ten customers."""
    return [
        path
        for customer_id in customer_ids
        for path in (
            f"/customers/{customer_id}/metrics",
            f"/customers/{customer_id}/anomalies",
        )
    ]


def clear_benchmark_cache(base_url: str, customer_ids: tuple[int, ...]) -> None:
    """Make the cached run start cold without disturbing unrelated Redis keys."""
    for customer_id in customer_ids:
        for suffix in ("metrics", "anomalies"):
            response = requests.delete(
                f"{base_url}/cache/customer:{customer_id}:{suffix}", timeout=5
            )
            response.raise_for_status()


def run_mode(base_url: str, paths: list[str], repeats: int, use_cache: bool) -> list[Measurement]:
    """Issue the same request set repeatedly and collect client/server timings."""
    measurements: list[Measurement] = []
    for _ in range(repeats):
        for path in paths:
            started_at = time.perf_counter()
            response = requests.get(
                f"{base_url}{path}", params={"use_cache": str(use_cache).lower()}, timeout=10
            )
            client_time_ms = (time.perf_counter() - started_at) * 1_000
            response.raise_for_status()
            measurements.append(
                Measurement(
                    client_time_ms=client_time_ms,
                    server_query_time_ms=float(response.headers["X-Query-Time-Ms"]),
                    cache_hit=response.headers["X-Cache-Hit"].lower() == "true",
                )
            )
    return measurements


def print_mode_summary(label: str, measurements: list[Measurement]) -> None:
    """Print a compact table line suitable for a terminal screenshot."""
    average_client = statistics.mean(item.client_time_ms for item in measurements)
    average_server = statistics.mean(item.server_query_time_ms for item in measurements)
    hit_rate = sum(item.cache_hit for item in measurements) / len(measurements) * 100
    print(
        f"{label:<10} {len(measurements):>8} {average_client:>16.2f} "
        f"{average_server:>16.2f} {hit_rate:>13.1f}%"
    )


def main() -> None:
    """Run cold/bypassed reads followed by a controlled warm-cache comparison."""
    parser = argparse.ArgumentParser(description="Benchmark Redis cache performance.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    args = parser.parse_args()
    if args.repeats <= 0:
        raise SystemExit("--repeats must be greater than zero.")

    base_url = args.base_url.rstrip("/")
    paths = benchmark_paths(DEFAULT_CUSTOMER_IDS)
    health_response = requests.get(f"{base_url}/health", timeout=5)
    health_response.raise_for_status()

    uncached = run_mode(base_url, paths, args.repeats, use_cache=False)
    clear_benchmark_cache(base_url, DEFAULT_CUSTOMER_IDS)
    cached = run_mode(base_url, paths, args.repeats, use_cache=True)

    uncached_average = statistics.mean(item.client_time_ms for item in uncached)
    cached_average = statistics.mean(item.client_time_ms for item in cached)
    speedup = uncached_average / cached_average if cached_average else 0.0

    print("\nCache benchmark (20 paths × " + str(args.repeats) + " repeats)")
    print(f"{'Mode':<10} {'Requests':>8} {'Avg client ms':>16} {'Avg DB/query ms':>16} {'Cache hit rate':>15}")
    print("-" * 72)
    print_mode_summary("uncached", uncached)
    print_mode_summary("cached", cached)
    print(f"\nSpeedup (uncached / cached): {speedup:.2f}x")


if __name__ == "__main__":
    main()
