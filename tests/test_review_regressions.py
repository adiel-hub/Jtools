"""Regressions for the bugs an independent review found before the first release."""

import asyncio
import json
import os
import subprocess
import sys

import httpx
import pytest

from jevcore.client import Jev
from jevcore.mock import POISON, MockJev, serve
from jevcore.questions import Noul
from jevcore.rubric import parse_scale
from jevtools import jgate, jgrep, jmatch, jpick, jroute, jtag, jwatch
from jevtools._shared import unescape
from tests.conftest import write


def test_jpick_top_n_with_few_more_lines_than_top_still_ranks(invoke):
    """13 lines, --top 7, group 12: the old code fell back to input order and lost the winner."""
    lines = [f"subject {i}" for i in range(13)]
    lines[9] = "URGENT outage now"
    res = invoke(jpick.main, ["most urgent", "--top", "7"], "\n".join(lines) + "\n")
    assert res.code == 0 and res.lines[0] == "URGENT outage now" and len(res.lines) == 7
    res = invoke(jpick.main, ["most urgent", "--top", "12"], "\n".join(lines) + "\n")
    assert res.code == 0 and res.lines[0] == "URGENT outage now" and len(res.lines) == 12


def test_jpick_non_final_rounds_always_narrow(invoke, mock):
    lines = [f"subject {i}" for i in range(30)]
    lines[25] = "URGENT outage now"
    res = invoke(jpick.main, ["most urgent", "--top", "10", "--group", "10"], "\n".join(lines) + "\n")
    assert res.code == 0 and res.lines[0] == "URGENT outage now" and len(res.lines) == 10
    assert all(len(b["state"]) <= 40 for b in mock.bodies)


def test_jgate_each_passthrough_keeps_input_order(invoke):
    mock = MockJev(delay=0.01)
    text = "".join(f"error line {i}\n" for i in range(30))
    res = invoke(jgate.main, ["an error", "--each", "-P", "-j", "8"], text, mock_override=mock)
    assert res.code == 0 and res.out == text


def test_jgate_fail_open_passthrough_echoes_input_on_fatal(invoke):
    dead = MockJev(script=[401])
    res = invoke(jgate.main, ["ready", "-P", "--fail-open"], "draft\n", mock_override=dead)
    assert res.code == 0 and res.out == "draft\n"
    dead = MockJev(script=[401])
    res = invoke(jgate.main, ["ready", "--each", "-P", "--fail-open"], "draft\nmore\n", mock_override=dead)
    assert res.code == 0 and res.out == "draft\nmore\n"


def test_jgate_each_with_unjudged_line_and_no_fit_fails_closed(invoke):
    res = invoke(jgate.main, ["an error", "--each"], f"fine\n{POISON} error\n")
    assert res.code == 4
    res = invoke(jgate.main, ["an error", "--each", "--fail-open"], f"fine\n{POISON} error\n")
    assert res.code == 1  # nothing judged fit; fail-open means "do not fail the pipe on the API"
    res = invoke(jgate.main, ["an error", "--each"], f"error here\n{POISON} error\n")
    assert res.code == 0  # a judged line fit; the unjudged one does not matter


def test_jgate_max_chars_default_depends_on_mode(invoke, mock):
    invoke(jgate.main, ["x"], "a" * 20000 + "\n")
    assert len(mock.bodies[-1]["state"]) == 20000  # whole input: 60,000 default
    invoke(jgate.main, ["x", "--each"], "a" * 20000 + "\n")
    assert len(mock.bodies[-1]["state"]) == 8000  # per line: the common 8,000 default


def test_jwatch_strict_is_honoured(invoke):
    res = invoke(jwatch.main, ["an error", "--strict"], f"{POISON} error\nerror\n")
    assert res.code == 4


def test_jgrep_unjudged_lines_render_like_matches(invoke, tmp_path):
    a = write(tmp_path, "a.txt", f"{POISON} whole file\n")
    b = write(tmp_path, "b.txt", "alpha\n")
    res = invoke(jgrep.main, ["alpha", "--whole", a, b])
    assert res.lines == [a, b] and res.code == 5  # file names, not contents
    res = invoke(jgrep.main, ["alpha", "-n", "-o"], f"alpha\n{POISON} x\n")
    assert res.lines == ["0.900\t1:alpha", "-\t2:POISON x"]
    c = write(tmp_path, "t.csv", f"id,text\n1,{POISON} row\n2,alpha\n")
    res = invoke(jgrep.main, ["alpha", "--csv", "--field", "text", c])
    assert res.lines == ["id,text", f"1,{POISON} row", "2,alpha"]  # header first, always
    res = invoke(jgrep.main, ["alpha", "--json"], f"{POISON} x\n")
    assert json.loads(res.out)["unjudged"] is True


def test_jgrep_dry_run_reports_missing_files(invoke, tmp_path):
    res = invoke(jgrep.main, ["alpha", str(tmp_path / "nope.txt"), "--dry-run"])
    assert res.code == 2 and "no such file" in res.err


def test_jmatch_none_has_no_score(invoke, tmp_path):
    a = write(tmp_path, "a.txt", "zzz\n")
    b = write(tmp_path, "b.txt", "Acme invoice paid\n")
    res = invoke(jmatch.main, [a, b, "same", "--unmatched"])
    assert res.lines == ["zzz\t\t-"]
    res = invoke(jmatch.main, [a, b, "same", "--json"])
    obj = json.loads(res.out)
    assert obj["score"] is None and obj["judged"] is True and obj["matched"] is False


def test_unescape_keeps_non_ascii():
    assert unescape("\\t") == "\t" and unescape("§") == "§" and unescape("\\t§→") == "\t§→" and unescape("|") == "|"


def test_jtag_non_ascii_separator(invoke):
    res = invoke(jtag.main, ["--labels", "bug,feature", "--sep", "§"], "crash\n")
    assert res.lines == ["bug§crash"]


def test_parse_scale_ignores_years_in_prose():
    assert parse_scale("how relevant to the 2024-2025 roadmap") is None
    assert parse_scale("how positive (0-100)") == (0.0, 100.0)
    assert parse_scale("rate from 1 to 5") == (1.0, 5.0)
    assert parse_scale("1-5", bare=True) == (1.0, 5.0)
    assert parse_scale("about 1-5 things", bare=True) is None


def test_jroute_unwritable_out_dir_is_a_clean_usage_error(invoke, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    res = invoke(jroute.main, ["a:x", "b:y", "-o", str(blocker / "sub")], "line\n")
    assert res.code == 2 and "cannot write" in res.err and "Traceback" not in res.err


async def test_unwritable_cache_falls_back_to_memory(creds, tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    reports = []
    jev = Jev(
        creds, cache_path=blocker / "answers.sqlite", transport=httpx.MockTransport(MockJev()), on_error=reports.append
    )
    try:
        await jev.ask("s", {"q": Noul("x")})
        await jev.ask("s", {"q": Noul("x")})
    finally:
        await jev.close()
    assert jev.meter.calls == 1 and jev.meter.cached == 1 and reports and "cache unavailable" in reports[0]


async def test_cancelling_one_sharer_does_not_cancel_the_others(creds):
    mock = MockJev(delay=0.1)
    async with Jev(creds, transport=httpx.MockTransport(mock), disk_cache=False) as jev:
        first = asyncio.ensure_future(jev.ask("same", {"q": Noul("x")}))
        second = asyncio.ensure_future(jev.ask("same", {"q": Noul("x")}))
        await asyncio.sleep(0.02)
        first.cancel()
        result = await second
    assert result["q"].probability in (0.1, 0.9) and len(mock.bodies) == 1


@pytest.mark.timeout(30)
def test_invalid_utf8_on_stdin_costs_one_character_not_the_stream(tmp_path):
    server = serve(MockJev())
    try:
        env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("JEV_")}
        env.pop("vercel_api_key", None)
        env.update(
            {
                "JEV_GATEWAY_URL": server.url,
                "JEV_GATEWAY_API_KEY": "t",
                "JEV_API": "gateway",
                "XDG_CACHE_HOME": str(tmp_path),
                "PYTHONIOENCODING": "utf-8:strict",
            }
        )
        proc = subprocess.run(
            [sys.executable, "-m", "jevtools.jtag", "--labels", "bug,feature", "--no-cache"],
            input=b"crash one\ncaf\xe9 two\ncrash three\n",
            capture_output=True,
            env=env,
            timeout=25,
        )
        out = proc.stdout.decode("utf-8")
        assert proc.returncode == 0, proc.stderr.decode()
        assert out.splitlines()[0] == "bug\tcrash one" and out.count("\n") == 3 and "caf� two" in out
    finally:
        server.shutdown()


def test_pipeline_errors_are_surfaced(invoke, monkeypatch):
    """A non-API failure inside judge names its cause instead of a bare 'could not be judged'."""
    from jevcore import client as client_module

    original = client_module.Jev.try_ask

    async def boom(self, state, questions):
        if "explode" in str(state):
            raise RuntimeError("cache exploded")
        return await original(self, state, questions)

    monkeypatch.setattr(client_module.Jev, "try_ask", boom)
    res = invoke(jtag.main, ["--labels", "bug,feature"], "crash\nexplode\n")
    assert res.code == 5 and "cache exploded" in res.err and res.lines[1] == "-\texplode"
