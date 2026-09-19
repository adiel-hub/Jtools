"""How fast and how cheap is one decision, and what do batching and concurrency buy?

    uv run python bench/latency.py --n 40

Three measurements, all live and uncached:

1. sequential single-question calls: p50/p95 latency, tokens and dollars per decision;
2. one call carrying 1, 4 and 16 questions about the same state: latency should stay flat;
3. N lines through the real `jgrep` at -j 1 and -j 8: throughput scaling.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

from _common import backend_info, run_tool, save
from jevcore import Jev, Noul, resolve

LINE = "user 12: this is the third time checkout has failed, I am done with this app"
DESCRIPTIONS = [
    "the writer is frustrated",
    "mentions a payment or checkout problem",
    "asks a question",
    "is written in English",
    "contains a number",
    "mentions a specific product feature",
    "is polite",
    "threatens to leave",
    "mentions a competitor",
    "is longer than ten words",
    "is about billing",
    "is about performance",
    "mentions a person by name",
    "is sarcastic",
    "asks for a refund",
    "reports a crash",
]


def percentile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(q * (len(xs) - 1)))]


async def single_calls(n: int) -> dict[str, object]:
    """Sequential single-question calls. Latency is the HTTP exchange itself (from the client's
    meter); wall time, which includes any rate-limit waits, is reported next to it."""
    wall: list[float] = []
    async with Jev(resolve(), disk_cache=False) as jev:
        for i in range(n):
            t0 = time.perf_counter()
            await jev.ask(f"{LINE} [{i}]", {"q": Noul(f'The text fits this description: "{DESCRIPTIONS[0]}"')})
            wall.append(time.perf_counter() - t0)
        request = list(jev.meter.latencies)
        tokens, cost, throttled = jev.meter.input_tokens, jev.meter.cost, jev.meter.throttled
    return {
        "n": n,
        "p50_ms": round(percentile(request, 0.5) * 1000),
        "p95_ms": round(percentile(request, 0.95) * 1000),
        "mean_ms": round(statistics.fmean(request) * 1000),
        "wall_p50_ms": round(percentile(wall, 0.5) * 1000),
        "wall_p95_ms": round(percentile(wall, 0.95) * 1000),
        "tokens_per_call": round(tokens / n),
        "dollars_per_call": round(cost / n, 8),
        "dollars_per_1000": round(cost / n * 1000, 5),
        "rate_limited": throttled,
        "note": "p50/p95 are the HTTP exchange; wall_* include rate-limit waits on a throttled key",
    }


async def batching(repeats: int) -> list[dict[str, object]]:
    out = []
    async with Jev(resolve(), disk_cache=False) as jev:
        for k in (1, 4, 16):
            before_calls = len(jev.meter.latencies)
            before_tokens, before_cost = jev.meter.input_tokens, jev.meter.cost
            for r in range(repeats):
                qs = {f"d{i}": Noul(f'The text fits this description: "{d}"') for i, d in enumerate(DESCRIPTIONS[:k])}
                await jev.ask(f"{LINE} [batch {k} {r}]", qs)
            lat = jev.meter.latencies[before_calls:]
            out.append(
                {
                    "questions_per_call": k,
                    "p50_ms": round(percentile(lat, 0.5) * 1000),
                    "tokens_per_call": round((jev.meter.input_tokens - before_tokens) / repeats),
                    "dollars_per_call": round((jev.meter.cost - before_cost) / repeats, 8),
                }
            )
    return out


def throughput(n: int) -> list[dict[str, object]]:
    lines = "".join(f"{LINE} #{i}\n" for i in range(n))
    out = []
    for jobs in (1, 8):
        code, _stdout, stderr, stats = run_tool(["jgrep", "-p", "0", "-j", str(jobs), DESCRIPTIONS[0]], lines)
        if code not in (0, 1):
            raise SystemExit(f"jgrep failed ({code}): {stderr[-400:]}")
        out.append(
            {
                "jobs": jobs,
                "lines": n,
                **stats,
                "lines_per_second": round(n / stats["seconds"], 2) if stats.get("seconds") else None,
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="sequential single calls (default 30)")
    ap.add_argument("--batch-repeats", type=int, default=3)
    ap.add_argument(
        "--throughput-lines", type=int, default=24, help="0 skips the throughput run (pointless on a throttled key)"
    )
    a = ap.parse_args()
    result: dict[str, object] = backend_info()
    print("single calls...", flush=True)
    result["single"] = asyncio.run(single_calls(a.n))
    print(result["single"], flush=True)
    print("batching...", flush=True)
    result["batching"] = asyncio.run(batching(a.batch_repeats))
    print(result["batching"], flush=True)
    if a.throughput_lines > 0:
        print("throughput...", flush=True)
        result["throughput"] = throughput(a.throughput_lines)
        print(result["throughput"], flush=True)
    else:
        result["throughput"] = []
    save("latency", result)


if __name__ == "__main__":
    main()
