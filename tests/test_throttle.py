"""Rate limits: one 429 brakes every request, Retry-After is honoured, and the run recovers."""

import asyncio
import time

import httpx

from jevcore.client import Jev
from jevcore.mock import MockJev
from jevcore.questions import Noul


async def test_a_429_brakes_the_whole_client_then_recovers(creds):
    mock = MockJev(script=[429])
    async with Jev(creds, transport=httpx.MockTransport(mock), disk_cache=False, timeout=10) as jev:
        t0 = time.perf_counter()
        results = await asyncio.gather(*(jev.ask(f"line {i}", {"q": Noul("x")}) for i in range(5)))
        elapsed = time.perf_counter() - t0
    assert all(results) and len(results) == 5
    assert jev.meter.throttled == 1 and jev.meter.retries >= 1
    # The braked requests waited about a second (the first throttle pause) instead of piling on.
    assert 0.8 <= elapsed < 5
    assert "rate-limited" in jev.meter.summary()


async def test_retry_after_header_is_honoured_but_capped(creds):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"Retry-After": "0.3"})
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.5}}, "usage": {}})

    async with Jev(creds, transport=httpx.MockTransport(handler), disk_cache=False, timeout=5) as jev:
        t0 = time.perf_counter()
        await jev.ask("s", {"q": Noul("x")})
        assert 0.3 <= time.perf_counter() - t0 < 2
    assert calls["n"] == 2


async def test_persistent_rate_limit_gives_up_at_the_deadline(creds):
    mock = MockJev(script=[429] * 50)
    async with Jev(creds, transport=httpx.MockTransport(mock), disk_cache=False, timeout=1.0) as jev:
        t0 = time.perf_counter()
        assert await jev.try_ask("s", {"q": Noul("x")}) is None
        assert time.perf_counter() - t0 < 2.0
    assert jev.meter.errors == 1
