"""The shared client: batching, caching, in-flight sharing, retries, deadlines, budget, fail-open."""

import asyncio
import json

import httpx
import pytest

from jevcore.auth import Credentials
from jevcore.backends import BACKENDS, VERCEL_URL
from jevcore.client import Jev
from jevcore.errors import AuthError, BudgetExceeded, JevError
from jevcore.mock import POISON, MockJev
from jevcore.questions import Choice, ChoiceAnswer, Noul, NoulAnswer, Score, ScoreAnswer


def client(mock: MockJev, creds: Credentials, **kw) -> Jev:
    kw.setdefault("disk_cache", False)
    return Jev(creds, transport=httpx.MockTransport(mock), **kw)


async def test_ask_batches_questions_into_one_request(mock, creds):
    async with client(mock, creds) as jev:
        answers = await jev.ask(
            "checkout failed, this is the worst",
            {
                "angry": Noul('The text fits this description: "angry customer"'),
                "route": Choice("route", {"billing": "payment", "bug": "crash"}),
                "urgency": Score('Rate how well the text fits this description: "urgent"', ["low", "mid", "high"]),
            },
        )
    assert len(mock.bodies) == 1 and set(mock.bodies[0]["questions"]) == {"angry", "route", "urgency"}
    assert isinstance(answers["angry"], NoulAnswer) and answers["angry"].probability == 0.9
    assert isinstance(answers["route"], ChoiceAnswer)
    assert isinstance(answers["urgency"], ScoreAnswer)
    assert jev.meter.calls == 1 and jev.meter.input_tokens > 0 and jev.meter.cost > 0
    assert jev.meter.model == "jev-1.13.0"


async def test_memory_cache_and_partial_misses(mock, creds):
    async with client(mock, creds) as jev:
        q1 = Noul("a")
        q2 = Noul("b")
        await jev.ask("state", {"x": q1})
        await jev.ask("state", {"x": q1, "y": q2})  # only y is sent
        await jev.ask("state", {"x": q1, "y": q2})  # nothing is sent
    assert [sorted(b["questions"]) for b in mock.bodies] == [["x"], ["y"]]
    assert jev.meter.calls == 2 and jev.meter.cached == 1


async def test_disk_cache_survives_a_new_client(mock, creds, tmp_path):
    path = tmp_path / "answers.sqlite"
    async with Jev(creds, transport=httpx.MockTransport(mock), cache_path=path) as jev:
        a = await jev.ask(
            "state", {"x": Noul("a"), "c": Choice("q", {"a": "A", "b": "B"}), "s": Score("q", ["l", "h"])}
        )
    async with Jev(creds, transport=httpx.MockTransport(mock), cache_path=path) as jev2:
        b = await jev2.ask(
            "state", {"x": Noul("a"), "c": Choice("q", {"a": "A", "b": "B"}), "s": Score("q", ["l", "h"])}
        )
    assert a == b and len(mock.bodies) == 1 and jev2.meter.calls == 0 and jev2.meter.cached == 1


async def test_cache_key_includes_model_and_question(mock, creds, tmp_path):
    path = tmp_path / "answers.sqlite"
    async with Jev(creds, transport=httpx.MockTransport(mock), cache_path=path, model="jev-1") as jev:
        await jev.ask("s", {"x": Noul("a")})
    async with Jev(creds, transport=httpx.MockTransport(mock), cache_path=path, model="jev-2") as jev:
        await jev.ask("s", {"x": Noul("a")})
        await jev.ask("s", {"x": Noul("b")})
    assert len(mock.bodies) == 3


async def test_identical_inflight_requests_share_one_call(creds):
    mock = MockJev(delay=0.05)
    async with client(mock, creds) as jev:
        results = await asyncio.gather(*(jev.ask("same line", {"q": Noul("x")}) for _ in range(10)))
    assert len(mock.bodies) == 1 and jev.meter.shared == 9
    assert all(r == results[0] for r in results)


async def test_concurrency_is_bounded(creds):
    mock = MockJev(delay=0.02)
    async with client(mock, creds, concurrency=4) as jev:
        await asyncio.gather(*(jev.ask(f"line {i}", {"q": Noul("x")}) for i in range(40)))
    assert mock.peak_in_flight <= 4 and len(mock.bodies) == 40


async def test_retries_transient_errors_then_succeeds(creds):
    mock = MockJev(script=[503, 429, 200])
    async with client(mock, creds, timeout=5) as jev:
        answers = await jev.ask("state", {"q": Noul("x")})
    assert isinstance(answers["q"], NoulAnswer)
    assert len(mock.bodies) == 3 and jev.meter.retries == 2 and jev.meter.calls == 1


async def test_non_retryable_error_is_raised_once(creds):
    mock = MockJev()
    async with client(mock, creds) as jev:
        with pytest.raises(JevError, match="HTTP 400"):
            await jev.ask(f"{POISON} state", {"q": Noul("x")})
    assert len(mock.bodies) == 1


async def test_auth_errors_are_fatal_and_not_retried(creds):
    mock = MockJev(script=[401, 401])
    async with client(mock, creds) as jev:
        with pytest.raises(AuthError, match="401"):
            await jev.ask("state", {"q": Noul("x")})
        with pytest.raises(AuthError):
            await jev.try_ask("state", {"q": Noul("x")})  # fatal errors pass through try_ask
    assert len(mock.bodies) == 2


async def test_try_ask_fails_open_and_reports_once_per_interval(creds):
    reports = []
    mock = MockJev(script=[500, 500, 500, 500, 500, 500, 500, 500])
    async with client(mock, creds, timeout=0.5, attempts=1, on_error=reports.append) as jev:
        assert await jev.try_ask("a", {"q": Noul("x")}) is None
        assert await jev.try_ask("b", {"q": Noul("x")}) is None
    assert jev.meter.errors == 2 and len(reports) == 2


async def test_error_reporter_throttles_to_once_per_interval(capsys):
    import io

    from jevcore.client import ErrorReporter

    buf = io.StringIO()
    report = ErrorReporter(buf, interval=60, prefix="t")
    for _ in range(5):
        report("boom")
    report.flush()
    text = buf.getvalue()
    assert text.count("boom") == 1 and "4 more errors were not shown" in text


async def test_total_deadline_covers_retries(creds):
    mock = MockJev(script=[503, 503, 503, 503, 503, 503], delay=0.05)
    async with client(mock, creds, timeout=0.2, attempts=10) as jev:
        with pytest.raises(JevError, match="gave up after"):
            await jev.ask("state", {"q": Noul("x")})
    assert len(mock.bodies) < 6


async def test_connection_errors_are_retried_then_reported(creds):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused")

    async with Jev(creds, transport=httpx.MockTransport(handler), timeout=1, attempts=3, disk_cache=False) as jev:
        with pytest.raises(JevError, match="ConnectError"):
            await jev.ask("state", {"q": Noul("x")})
    assert calls == 3


async def test_budget_stops_the_run(creds):
    mock = MockJev(fixed_tokens=1_000_000)  # ~$0.042 per call
    async with client(mock, creds, budget=0.05) as jev:
        await jev.ask("a", {"q": Noul("x")})
        await jev.ask("b", {"q": Noul("x")})
        with pytest.raises(BudgetExceeded):
            await jev.ask("c", {"q": Noul("x")})
        # Cached answers stay free even over budget.
        assert await jev.ask("a", {"q": Noul("x")})


async def test_vercel_backend_uses_its_wire_and_headers(mock):
    creds = Credentials(BACKENDS["vercel"], "vck_test", VERCEL_URL)
    async with client(mock, creds) as jev:
        answers = await jev.ask(
            "checkout failed",
            {
                "angry": Noul('The text fits this description: "angry"'),
                "c": Choice("q", {"a": "A", "b": "B"}),
            },
        )
    body, headers = mock.bodies[0], mock.headers[0]
    assert "model" not in body and body["questions"]["angry"]["type"] == "boolean"
    assert headers["ai-model-id"] == "typesafe-ai/jev" and headers["authorization"] == "Bearer vck_test"
    assert isinstance(answers["angry"], NoulAnswer)
    assert isinstance(answers["c"], ChoiceAnswer) and answers["c"].confidence == 0.8
    assert jev.meter.cost > 0 and jev.meter.model == "typesafe-ai/jev"


async def test_model_override_and_env(mock, creds, monkeypatch):
    async with client(mock, creds, model="typesafe/jev-1.13") as jev:
        await jev.ask("s", {"q": Noul("x")})
    assert mock.bodies[-1]["model"] == "typesafe/jev-1.13"
    monkeypatch.setenv("JEV_MODEL", "jev-preview")
    async with client(mock, creds) as jev:
        await jev.ask("s", {"q": Noul("x")})
    assert mock.bodies[-1]["model"] == "jev-preview"


async def test_malformed_response_is_a_jev_error_and_not_cached(creds, tmp_path):
    def handler(request):
        return httpx.Response(200, json={"answers": {"q": {"noul": "high"}}})

    async with Jev(creds, transport=httpx.MockTransport(handler), cache_path=tmp_path / "c.sqlite") as jev:
        with pytest.raises(JevError, match="invalid answer"):
            await jev.ask("s", {"q": Noul("x")})
        with pytest.raises(JevError):
            await jev.ask("s", {"q": Noul("x")})  # still not served from cache


async def test_empty_question_set_is_a_noop(mock, creds):
    async with client(mock, creds) as jev:
        assert await jev.ask("s", {}) == {}
    assert mock.bodies == []


async def test_ping_reports_latency_and_usage(mock, creds):
    async with client(mock, creds) as jev:
        seconds, usage = await jev.ping()
    assert seconds >= 0 and usage.input_tokens > 0


async def test_meter_summary_and_dict(mock, creds):
    async with client(mock, creds) as jev:
        await jev.ask("s", {"q": Noul("x")})
    text = jev.meter.summary()
    assert "1 calls" in text and "$" in text and "p50" in text
    d = jev.meter.as_dict()
    assert d["calls"] == 1 and d["latency_p50_ms"] is not None
    json.dumps(d)
