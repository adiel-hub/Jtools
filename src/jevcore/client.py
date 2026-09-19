"""The async Jev client every tool shares.

One method matters: :meth:`Jev.ask` answers a set of questions about one state. It

- packs every question about the same state into ONE request (Jev evaluates them in parallel, so
  extra questions cost tokens but no time),
- bounds requests in flight with a semaphore (default 20),
- serves repeats from an in-memory cache and, unless disabled, a SQLite cache on disk,
- shares one HTTP call between identical requests already in the air,
- retries transient failures with jittered backoff inside a total deadline,
- meters tokens, dollars and latency, and stops at a dollar budget,
- and reports errors to stderr at most once per minute, so a dead endpoint cannot flood a pipe.

:meth:`Jev.try_ask` is the fail-open form: it returns ``None`` where :meth:`Jev.ask` would raise a
non-fatal :class:`~jevcore.errors.JevError`. Tools use it unless the user passes ``--strict``.
"""

from __future__ import annotations

import asyncio
import os
import random
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import IO, Any

import httpx

from . import __version__
from .auth import Credentials
from .backends import Backend
from .cache import DiskCache, LayeredCache, cache_key
from .errors import AuthError, BudgetExceeded, JevError, JevFatal
from .questions import Answer, Question, State
from .wire import Usage

RETRYABLE = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
THROTTLED = frozenset({429, 529})
MAX_THROTTLE_PAUSE = 15.0
TRICKLE_SECONDS = 120.0  # after a 429, send one request at a time for this long
DEFAULT_CONCURRENCY = int(os.environ.get("JEV_CONCURRENCY", "20") or 20)
FATAL_AUTH = frozenset({401, 402, 403})
# TypeSafe reports tokens, not dollars. Its list price is $0.042 per million input tokens.
PRICE_PER_MTOK = float(os.environ.get("JEV_PRICE_PER_MTOK", "0.042"))
DEFAULT_TIMEOUT = float(os.environ.get("JEV_TIMEOUT", "15") or 15)
DEFAULT_ATTEMPTS = 5
ERROR_REPORT_INTERVAL = 60.0


@dataclass
class Meter:
    """What a run cost. Printed with ``--stats`` or whenever stderr is a terminal."""

    calls: int = 0
    cached: int = 0
    shared: int = 0
    retries: int = 0
    throttled: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    model: str = ""
    latencies: list[float] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    def record(self, usage: Usage, seconds: float, fallback_model: str) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cost += usage.cost if usage.cost is not None else usage.input_tokens * PRICE_PER_MTOK / 1e6
        self.latencies.append(seconds)
        self.model = usage.model or fallback_model

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def percentile(self, q: float) -> float | None:
        if not self.latencies:
            return None
        ordered = sorted(self.latencies)
        idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
        return ordered[idx]

    def summary(self) -> str:
        parts = [f"{self.calls:,} calls", f"{self.cached:,} cached"]
        if self.shared:
            parts.append(f"{self.shared:,} shared")
        if self.retries:
            parts.append(f"{self.retries:,} retries")
        if self.throttled:
            parts.append(f"{self.throttled:,} rate-limited")
        if self.errors:
            parts.append(f"{self.errors:,} errors")
        if self.calls:
            parts.append(f"{self.input_tokens:,} tokens")
            parts.append(f"${self.cost:.4f}" if self.cost >= 0.001 else f"${self.cost:.6f}")
            p50 = self.percentile(0.5)
            if p50 is not None:
                parts.append(f"p50 {p50 * 1000:.0f} ms")
        parts.append(f"{self.elapsed:.1f}s")
        return "; ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        p50, p95 = self.percentile(0.5), self.percentile(0.95)
        return {
            "calls": self.calls,
            "cached": self.cached,
            "shared": self.shared,
            "retries": self.retries,
            "throttled": self.throttled,
            "errors": self.errors,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost, 6),
            "model": self.model,
            "latency_p50_ms": round(p50 * 1000) if p50 is not None else None,
            "latency_p95_ms": round(p95 * 1000) if p95 is not None else None,
            "elapsed_s": round(self.elapsed, 2),
        }


class ErrorReporter:
    """Prints an error at most once per interval; counts what it swallowed in between."""

    def __init__(
        self, stream: IO[str] | None = None, interval: float = ERROR_REPORT_INTERVAL, prefix: str = "jev"
    ) -> None:
        self.stream = stream
        self.interval = interval
        self.prefix = prefix
        self.total = 0
        self._suppressed = 0
        self._last = float("-inf")

    def __call__(self, message: str) -> None:
        self.total += 1
        now = time.monotonic()
        if now - self._last < self.interval:
            self._suppressed += 1
            return
        stream = self.stream or sys.stderr
        suffix = f" (and {self._suppressed:,} more since the last report)" if self._suppressed else ""
        print(f"{self.prefix}: {message}{suffix}", file=stream, flush=True)
        self._suppressed = 0
        self._last = now

    def flush(self) -> None:
        if self._suppressed:
            stream = self.stream or sys.stderr
            print(f"{self.prefix}: {self._suppressed:,} more errors were not shown", file=stream, flush=True)
            self._suppressed = 0


class Jev:
    """Async client. Create one per run; ``await close()`` when done (or use ``async with``)."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        attempts: int = DEFAULT_ATTEMPTS,
        concurrency: int = DEFAULT_CONCURRENCY,
        budget: float = 0.0,
        disk_cache: bool = True,
        cache_path: Any = None,
        transport: httpx.AsyncBaseTransport | None = None,
        on_error: Callable[[str], None] | None = None,
        prefix: str = "jev",
    ) -> None:
        self.backend: Backend = credentials.backend
        self.url = credentials.url
        self.model = (model or os.environ.get("JEV_MODEL") or "").strip() or self.backend.model
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.concurrency = max(1, concurrency)
        self.budget = max(0.0, budget)
        self.meter = Meter()
        self.report: Callable[[str], None] = on_error or ErrorReporter(prefix=prefix)
        self._sem = asyncio.Semaphore(self.concurrency)
        self._brake_until = 0.0  # monotonic time before which nothing is sent (after a 429/529)
        self._trickle_until = 0.0  # while set, requests go out one at a time (a rate-limited tier)
        self._trickle = asyncio.Lock()
        self._flights: dict[str, asyncio.Future[dict[str, Answer]]] = {}
        disk: DiskCache | None = None
        if disk_cache:
            try:
                disk = DiskCache(cache_path)
            except (OSError, sqlite3.Error) as e:
                # An unwritable cache dir must not stop a pipeline; run from memory and say so once.
                self.report(f"answer cache unavailable ({e}); continuing without it")
        self._cache = LayeredCache(disk)
        self._closed = False
        headers = {
            "Authorization": f"Bearer {credentials.key}",
            "User-Agent": f"jev-tools/{__version__} (+https://github.com/adiel-hub/Jtools)",
            "HTTP-Referer": "https://github.com/adiel-hub/Jtools",
            "X-Title": "j-tools",
            **self.backend.wire.headers(self.model),
        }
        self.http = httpx.AsyncClient(
            headers=headers,
            limits=httpx.Limits(max_connections=self.concurrency + 4, max_keepalive_connections=self.concurrency + 4),
            transport=transport,
            timeout=httpx.Timeout(timeout),
        )

    async def __aenter__(self) -> Jev:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Shielded calls may still be in the air after their askers were cancelled; end them quietly.
        flights = [f for f in self._flights.values() if not f.done()]
        for f in flights:
            f.cancel()
        if flights:
            await asyncio.gather(*flights, return_exceptions=True)
        await self.http.aclose()
        self._cache.close()

    # ---------------------------------------------------------------- asking

    async def ask(self, state: State, questions: Mapping[str, Question]) -> dict[str, Answer]:
        """Answer every question about one state. Only questions missing from the cache are sent."""
        if not questions:
            return {}
        keys = {qid: cache_key(self.model, state, q) for qid, q in questions.items()}
        answers: dict[str, Answer] = {}
        for qid, key in keys.items():
            hit = self._cache.get(key)
            if hit is not None:
                answers[qid] = hit
        misses = {qid: q for qid, q in questions.items() if qid not in answers}
        if not misses:
            self.meter.cached += 1
            return answers
        if self.budget and self.meter.cost >= self.budget:
            raise BudgetExceeded(f"stopped at the ${self.budget:.2f} budget; raise it with --budget")

        # Identical requests already in the air share one call; logs repeat themselves a lot.
        flight_key = "|".join(sorted(keys[qid] for qid in misses))
        future = self._flights.get(flight_key)
        if future is None:
            future = asyncio.ensure_future(self._call(state, misses, {qid: keys[qid] for qid in misses}))
            self._flights[flight_key] = future
            future.add_done_callback(lambda _f: self._flights.pop(flight_key, None))
        else:
            self.meter.shared += 1
        # Shield: cancelling one asker must not cancel the call the other sharers are waiting on.
        by_key = await asyncio.shield(future)
        return answers | {qid: by_key[keys[qid]] for qid in misses}

    async def try_ask(self, state: State, questions: Mapping[str, Question]) -> dict[str, Answer] | None:
        """Fail-open :meth:`ask`: report and return ``None`` on a per-request error; re-raise fatal ones."""
        try:
            return await self.ask(state, questions)
        except JevFatal:
            raise
        except JevError as e:
            self.meter.errors += 1
            self.report(str(e))
            return None

    async def ping(self) -> tuple[float, Usage]:
        """One tiny real call. Returns (seconds, usage). Used by ``jtools doctor`` and live tests."""
        from .questions import Noul

        t0 = time.perf_counter()
        wire = self.backend.wire
        body = wire.body(self.model, "The sky is blue.", {"q": Noul("The text mentions a colour.")})
        response = await self.http.post(self.url, json=body)
        seconds = time.perf_counter() - t0
        data = _json(response)
        if response.status_code in FATAL_AUTH:
            raise AuthError(f"{self.backend.name} said {response.status_code}: {wire.error_detail(data)}")
        if response.status_code != 200:
            raise JevError(f"HTTP {response.status_code}: {wire.error_detail(data) or response.text[:200]}")
        _, usage = wire.parse(data, {"q": Noul("The text mentions a colour.")})
        return seconds, usage

    # -------------------------------------------------------------- internals

    async def _wait_for_brake(self, deadline: float) -> None:
        brake = self._brake_until - time.monotonic()
        if brake > 0:
            await asyncio.sleep(min(brake, max(0.0, deadline - time.monotonic())))

    async def _call(self, state: State, questions: Mapping[str, Question], keys: dict[str, str]) -> dict[str, Answer]:
        """One request, retried inside a total time budget. Returns answers by cache key."""
        wire = self.backend.wire
        body = wire.body(self.model, state, questions)
        deadline = time.monotonic() + self.timeout
        last = "no attempt made"
        attempt = throttles = 0
        async with self._sem:
            while True:
                # After a rate limit every request waits; one 429 must not turn into twenty.
                await self._wait_for_brake(deadline)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                t0 = time.perf_counter()
                try:
                    # httpx bounds each socket wait, not the whole exchange; wait_for bounds the
                    # complete request, including a body that dribbles in.
                    if time.monotonic() < self._trickle_until:
                        # A rate-limited tier: releasing twenty waiters at once just earns twenty
                        # more 429s. Send one at a time until the limit has been quiet for a while.
                        async with self._trickle:
                            await self._wait_for_brake(deadline)
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            t0 = time.perf_counter()
                            response = await asyncio.wait_for(
                                self.http.post(self.url, json=body, timeout=remaining), timeout=remaining
                            )
                    else:
                        response = await asyncio.wait_for(
                            self.http.post(self.url, json=body, timeout=remaining), timeout=remaining
                        )
                except TimeoutError:
                    last = "deadline exceeded"
                    break
                except httpx.TransportError as e:
                    last = type(e).__name__
                else:
                    data = _json(response)
                    if response.status_code == 200 and "answers" in data:
                        answers, usage = wire.parse(data, questions)
                        self.meter.record(usage, time.perf_counter() - t0, self.model)
                        out: dict[str, Answer] = {}
                        for qid, answer in answers.items():
                            key = keys[qid]
                            self._cache.put(key, answer)
                            out[key] = answer
                        return out
                    detail = wire.error_detail(data) or response.text[:200].strip()
                    if response.status_code in FATAL_AUTH:
                        raise AuthError(f"{self.backend.name} said {response.status_code}: {detail}")
                    if response.status_code not in RETRYABLE:
                        raise JevError(f"HTTP {response.status_code}: {detail or 'unexpected response'}")
                    last = f"HTTP {response.status_code}" + (f" ({detail})" if detail else "")
                    if response.status_code in THROTTLED:
                        # A rate limit is not a failure of ours: wait it out, as long as --timeout allows.
                        throttles += 1
                        self.meter.throttled += 1
                        pause = _retry_after(response) or min(MAX_THROTTLE_PAUSE, 1.0 * 2 ** min(throttles - 1, 4))
                        now = time.monotonic()
                        self._brake_until = max(self._brake_until, now + pause)
                        self._trickle_until = now + TRICKLE_SECONDS
                        continue
                attempt += 1
                if attempt >= self.attempts:
                    break
                self.meter.retries += 1
                pause = 0.2 * 2 ** (attempt - 1) + random.random() * 0.1  # jitter, not security
                await asyncio.sleep(max(0.0, min(pause, deadline - time.monotonic())))
        raise JevError(f"gave up after {self.timeout:g}s ({last})")


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds from a Retry-After header, when the API sends one."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, min(60.0, float(raw)))
    except ValueError:
        return None


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
