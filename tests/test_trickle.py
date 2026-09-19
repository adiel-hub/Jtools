"""After a rate limit the client sends one request at a time instead of releasing a burst."""

import asyncio
import time

import httpx
import pytest

from jevcore.client import Jev
from jevcore.errors import UsageError
from jevcore.mock import MockJev
from jevcore.questions import Noul


async def test_requests_are_serialised_after_a_429_and_burst_again_later(creds, monkeypatch):
    from jevcore import client as client_module

    monkeypatch.setattr(client_module, "TRICKLE_SECONDS", 3.0)  # must outlast the 1 s brake after the 429
    mock = MockJev(script=[429], delay=0.03)
    async with Jev(creds, transport=httpx.MockTransport(mock), disk_cache=False, timeout=10) as jev:
        await jev.ask("first", {"q": Noul("x")})  # 429, then the retry succeeds: trickle mode is on
        mock.peak_in_flight = 0
        await asyncio.gather(*(jev.ask(f"line {i}", {"q": Noul("x")}) for i in range(8)))
        # During the trickle window no two requests were in flight together.
        assert mock.peak_in_flight == 1
        mock.peak_in_flight = 0
        await asyncio.sleep(2.2)  # the window closes; concurrency comes back
        await asyncio.gather(*(jev.ask(f"later {i}", {"q": Noul("x")}) for i in range(8)))
        assert mock.peak_in_flight >= 4
    assert jev.meter.throttled == 1 and jev.meter.calls == 17


async def test_trickle_respects_the_deadline(creds):
    mock = MockJev(script=[429] * 100)
    async with Jev(creds, transport=httpx.MockTransport(mock), disk_cache=False, timeout=1.0) as jev:
        t0 = time.perf_counter()
        results = await asyncio.gather(*(jev.try_ask(f"line {i}", {"q": Noul("x")}) for i in range(4)))
        assert time.perf_counter() - t0 < 3.0
    assert results == [None, None, None, None] and jev.meter.errors == 4


def test_the_environment_defaults_are_read_per_run(monkeypatch):
    """Not at import: a process that fixes a variable and tries again must be believed."""
    from jevcore.client import env_defaults

    monkeypatch.setenv("JEV_CONCURRENCY", "3")
    monkeypatch.setenv("JEV_TIMEOUT", "45")
    assert env_defaults() == (3, 45.0)

    monkeypatch.setenv("JEV_TIMEOUT", "45s")
    with pytest.raises(UsageError, match="JEV_TIMEOUT"):
        env_defaults()

    monkeypatch.setenv("JEV_TIMEOUT", "45")  # fixed, and believed straight away
    assert env_defaults() == (3, 45.0)


@pytest.mark.parametrize("value", ["-5", "inf", "nan", "0"])
def test_an_unusable_timeout_names_the_variable(monkeypatch, value):
    from jevcore.client import env_defaults

    monkeypatch.setenv("JEV_TIMEOUT", value)
    with pytest.raises(UsageError, match=f"JEV_TIMEOUT={value!r}"):
        env_defaults()
