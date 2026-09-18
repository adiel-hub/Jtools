"""The mock must be predictable (tests depend on it) and the IO helpers must be safe."""

import io

from jevcore.io import Output, fmt_p, redact, wants_colour
from jevcore.mock import MockJev, Rule, serve
from jevcore.rubric import parse_buckets, parse_labels, parse_levels, parse_scale


def test_mock_noul_keyword_heuristic():
    m = MockJev()
    q = {"type": "noul", "instructions": 'The text fits this description: "angry customer"'}
    assert m.answer("I am furious, worst service ever", "q", q)["noul"] == 0.9
    assert m.answer("thanks, all good", "q", q)["noul"] == 0.1


def test_mock_choice_picks_by_option_words_and_candidates():
    m = MockJev()
    q = {"type": "choice", "instructions": "route", "criteria": {"billing": "payment", "bug": "crash"}}
    a = m.answer("the app crashes on launch", "q", q)
    assert a["choice"] == "bug" and abs(sum(a["probabilities"].values()) - 1) < 1e-6
    state = [{"id": "c1", "text": "thanks"}, {"id": "c2", "text": "this is unacceptable!!"}]
    q2 = {
        "type": "choice",
        "instructions": 'Choose the candidate that best fits this description: "angry"',
        "criteria": {"c1": "c1", "c2": "c2"},
    }
    assert m.answer(state, "q", q2)["choice"] == "c2"


def test_mock_score_and_rules():
    m = MockJev(rules=[Rule({"type": "noul", "noul": 0.42}, state_contains="magic")])
    q = {
        "type": "score",
        "instructions": 'Rate how well the text fits this description: "urgent"',
        "criteria": ["a", "b", "c", "d", "e"],
    }
    a = m.answer("this is urgent, outage now", "q", q)
    assert a["score"] > 1 and set(a["probabilities"]) == {"0", "1", "2", "3", "4"}
    assert m.answer("magic words", "q", {"type": "noul", "instructions": "x"})["noul"] == 0.42


def test_mock_server_speaks_systemone_over_http():
    import json
    import urllib.request

    server = serve()
    try:
        body = json.dumps(
            {
                "model": "jev-latest",
                "state": "I am furious",
                "questions": {"q": {"type": "noul", "instructions": 'fits "angry"'}},
            }
        ).encode()
        req = urllib.request.Request(server.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        assert data["answers"]["q"]["noul"] == 0.9 and server.mock.bodies
    finally:
        server.shutdown()


def test_output_flushes_and_colours_only_when_asked(monkeypatch):
    buf = io.StringIO()
    out = Output(buf, colour="never")
    out.scored(0.9, "hello")
    out.json({"a": 1})
    assert buf.getvalue() == '0.900\thello\n{"a": 1}\n' and out.lines == 2
    buf2 = io.StringIO()
    Output(buf2, colour="always").scored(0.9, "x")
    assert "\x1b[32m" in buf2.getvalue()
    monkeypatch.setenv("NO_COLOR", "1")
    assert not wants_colour("auto", buf2)
    assert fmt_p(None) == "-" and fmt_p(0.5) == "0.500"


def test_redact_masks_emails_digits_and_tokens():
    s = redact("mail bob@example.com card 4111111111111111 key sk-abcdefghijklmnop " + "x" * 300)
    assert "bob@" not in s and "4111111111111111" not in s and "sk-abcdefghijklmnop" not in s
    assert len(s) <= 120 and s.endswith("…")


def test_rubric_parsers():
    assert parse_scale("how positive (0-100)") == (0.0, 100.0)
    assert parse_scale("rate 1 to 5") == (1.0, 5.0)
    assert parse_scale("plain") is None
    assert parse_levels("low, mid ,high") == ("low", "mid", "high")
    assert parse_labels("bug,feature:a new capability") == {"bug": "bug", "feature": "a new capability"}
    assert parse_buckets(["sales:a lead", "spam:junk"]) == {"sales": "a lead", "spam": "junk"}
    import pytest

    from jevcore.errors import UsageError

    with pytest.raises(UsageError):
        parse_levels("one")
    with pytest.raises(UsageError):
        parse_labels("only")
    with pytest.raises(UsageError):
        parse_buckets(["noseparator", "b:x"])
    with pytest.raises(UsageError):
        parse_buckets(["bad name:x", "b:x"])
