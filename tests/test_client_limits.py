"""The three limits a run is promised: a dollar budget, a per-request deadline, a rate-limit brake.

Each of these was wrong in a way that only showed up under concurrency, which is the only way the
tools ever use the client, so each one gets a concurrent test rather than a sequential one.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import httpx
import pytest

from jevcore.auth import Credentials
from jevcore.client import Jev
from jevcore.errors import BudgetExceeded, JevError
from jevcore.mock import MockJev
from jevcore.questions import Noul


def client(mock: MockJev, creds: Credentials, **kw) -> Jev:
    kw.setdefault("disk_cache", False)
    return Jev(creds, transport=httpx.MockTransport(mock), **kw)


async def test_the_budget_holds_when_every_call_starts_at_once(creds):
    """Two hundred asks in one gather must not all clear a budget that one of them exhausts."""
    mock = MockJev(fixed_tokens=1_000_000)  # about $0.042 a call at list price
    budget = 0.20
    async with client(mock, creds, budget=budget, concurrency=20) as jev:
        results = await asyncio.gather(
            *(jev.ask(f"line {i}", {"q": Noul("a question")}) for i in range(200)), return_exceptions=True
        )
    refused = [r for r in results if isinstance(r, BudgetExceeded)]
    # The reservation bounds the overshoot to roughly one call's estimate, not to -j calls.
    assert jev.meter.cost <= budget * 1.5, f"spent ${jev.meter.cost:.4f} against a ${budget:.2f} budget"
    assert refused, "nothing was refused; the budget did nothing"
    assert len(mock.bodies) == jev.meter.calls < 200


async def test_waiting_for_a_j_slot_is_not_charged_to_the_timeout(creds):
    """--timeout is what one request may take. Queue time behind -j is not the backend being slow."""
    mock = MockJev(delay=0.05)
    async with client(mock, creds, timeout=1.0, concurrency=2) as jev:
        results = await asyncio.gather(
            *(jev.ask(f"line {i}", {"q": Noul("a question")}) for i in range(20)), return_exceptions=True
        )
    failures = [r for r in results if isinstance(r, Exception)]
    assert not failures, f"{len(failures)} of 20 timed out while queued: {failures[:1]}"
    assert len(mock.bodies) == 20


async def test_a_retry_after_longer_than_the_timeout_fails_fast_and_says_why(creds):
    """A 60s Retry-After against a 15s timeout is not something to sleep through in silence."""
    mock = MockJev(script=[429])
    async with client(mock, creds, timeout=0.5, concurrency=4) as jev:
        with pytest.raises(JevError):
            await jev.ask("first", {"q": Noul("a question")})  # earns the brake
        started = time.monotonic()
        with pytest.raises(JevError) as caught:
            await jev.ask("second", {"q": Noul("a question")})
        waited = time.monotonic() - started
    assert "rate limited" in str(caught.value) and "--timeout" in str(caught.value)
    assert "no attempt made" not in str(caught.value)
    assert waited < 0.4, f"waited {waited:.2f}s to report a limit it could never outlast"
    assert jev.meter.throttled >= 2, "a request the brake refused was not counted as rate-limited"


async def test_a_brake_shorter_than_the_timeout_is_still_waited_out(creds):
    """The fast failure above must not turn an ordinary short pause into an error."""
    mock = MockJev(script=[429])
    async with client(mock, creds, timeout=10.0) as jev:
        answers = await jev.ask("line", {"q": Noul("a question")})
    assert answers["q"] is not None and jev.meter.throttled == 1 and len(mock.bodies) == 2


async def test_cancelling_one_sharer_leaves_no_stray_task_exception(creds, capsys):
    """The shielded call outlives its asker; its exception must be read, not printed by asyncio."""
    mock = MockJev(script=[500, 500, 500, 500, 500], delay=0.02)
    jev = client(mock, creds, timeout=0.3, attempts=2)
    task = asyncio.ensure_future(jev.ask("line", {"q": Noul("a question")}))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.4)  # let the shielded call finish and be garbage collected
    await jev.close()
    assert "never retrieved" not in capsys.readouterr().err


async def test_a_cache_path_may_be_a_string(creds, tmp_path):
    """The parameter used to be typed Any, and a str crashed inside the cache."""
    async with Jev(creds, transport=httpx.MockTransport(MockJev()), cache_path=str(tmp_path / "a.sqlite")) as jev:
        await jev.ask("line", {"q": Noul("a question")})
    assert (tmp_path / "a.sqlite").exists()


@pytest.mark.parametrize("value", ["15s", "abc", "12 seconds"])
def test_a_typo_in_an_environment_default_does_not_stop_the_command(value, tmp_path):
    """JEV_TIMEOUT=15s is an easy mistake; --help must still print the flag that explains it."""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TYPESAFE_API_KEY": "k",
        "JEV_TIMEOUT": value,
        "JEV_CONCURRENCY": value,
        "PYTHONPATH": "src",
    }
    done = subprocess.run(
        [sys.executable, "-m", "jevtools.jgrep", "--help"], capture_output=True, text=True, env=env, check=False
    )
    assert done.returncode == 0, done.stderr[-500:]
    assert "--timeout" in done.stdout
    assert "ignoring JEV_TIMEOUT" in done.stderr
