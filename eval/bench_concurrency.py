"""Concurrent search load test against a running server (any profile).

    uv run python eval/bench_concurrency.py --url http://127.0.0.1:8000 --user alice --password ... \
        --concurrency 1 4 8 16 --requests 200

Uses the labelled queries from eval/queries.jsonl in round-robin. Reports client-side end-to-end latency
(p50/p95/p99), throughput and errors per concurrency level. Record hardware and corpus size with results.
"""

import argparse
import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 4, 8])
    ap.add_argument("--requests", type=int, default=200)
    a = ap.parse_args()
    queries = [json.loads(x)["query"] for x in (Path(__file__).parent / "queries.jsonl").read_text().splitlines() if x]
    with httpx.Client(base_url=a.url, timeout=30) as c:
        tok = c.post("/api/auth/login", json={"username": a.user, "password": a.password}).json()["token"]
    headers = {"Authorization": f"Bearer {tok}"}

    for conc in a.concurrency:
        lat, errors = [], 0
        local = threading.local()

        def one(i):
            if not hasattr(local, "client"):  # one keep-alive connection per thread
                local.client = httpx.Client(base_url=a.url, timeout=30, headers=headers)
            t = time.perf_counter()
            r = local.client.post("/api/search", json={"query": queries[i % len(queries)]})
            return (time.perf_counter() - t) * 1000, r.status_code

        t0 = time.perf_counter()
        with ThreadPoolExecutor(conc) as ex:
            for ms, status in ex.map(one, range(a.requests)):
                lat.append(ms)
                errors += status != 200
        wall = time.perf_counter() - t0
        q = statistics.quantiles(lat, n=100)
        print(f"concurrency={conc:3d} requests={a.requests} errors={errors} throughput={a.requests / wall:.1f} req/s "
              f"p50={q[49]:.1f}ms p95={q[94]:.1f}ms p99={q[98]:.1f}ms")


if __name__ == "__main__":
    main()
