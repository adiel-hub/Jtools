"""Live smoke tests: one real call per tool, proving the wire format against a real endpoint.

Skipped cleanly when no key is configured. With a key, the whole file costs well under a cent.
Run them alone with ``pytest -m live``.
"""

import io
import json
import sys

import httpx
import pytest

from jevcore.auth import available, resolve
from jevcore.client import Jev
from jevcore.questions import Choice, ChoiceAnswer, Noul, NoulAnswer, Score, ScoreAnswer
from jevtools import jgate, jgrep, jhead, jmatch, jpick, jroute, jsort, jtag, juniq, jwatch

pytestmark = pytest.mark.live

if not available():
    pytest.skip("no Jev API key in the environment", allow_module_level=True)


@pytest.fixture(autouse=True)
def no_disk_cache(monkeypatch):
    monkeypatch.setenv("JEV_NO_CACHE", "1")


def live(main, argv, stdin="", monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv) + ["--no-cache"], out=out, err=err)
    return code, out.getvalue(), err.getvalue()


async def test_all_three_primitives_round_trip():
    creds = resolve()
    async with Jev(creds, disk_cache=False) as jev:
        answers = await jev.ask(
            "user 12: this is the third time checkout has failed, I am done with this app",
            {
                "angry": Noul("The writer is frustrated or angry."),
                "route": Choice(
                    "Route this message.",
                    {"billing": "payment problems", "bug": "software malfunction", "praise": "positive feedback"},
                ),
                "urgency": Score("How urgent is this?", ["low", "medium", "high"]),
            },
        )
    angry, route, urgency = answers["angry"], answers["route"], answers["urgency"]
    assert isinstance(angry, NoulAnswer) and angry.probability > 0.7
    assert (
        isinstance(route, ChoiceAnswer)
        and route.choice in ("billing", "bug")
        and abs(sum(route.probabilities.values()) - 1) < 0.05
    )
    assert isinstance(urgency, ScoreAnswer) and urgency.normalized > 0.5 and urgency.levels == 3
    assert jev.meter.calls == 1 and jev.meter.input_tokens > 100
    print(f"\n[{creds.backend.name}] {jev.meter.summary()} model={jev.meter.model}", file=sys.stderr)


async def test_bad_key_is_an_auth_error():
    from jevcore.auth import Credentials
    from jevcore.errors import AuthError

    creds = resolve()
    bad = Credentials(creds.backend, "definitely-not-a-key", creds.url)
    async with Jev(bad, disk_cache=False, attempts=1) as jev:
        with pytest.raises(AuthError):
            await jev.ask("x", {"q": Noul("The text is short.")})


def test_jsort_ranks_the_angry_line_first(monkeypatch):
    text = (
        "Love the new dashboard, thanks team!\n"
        "This is the third time checkout has failed, I am done with this app!!\n"
        "How do I export my data to CSV?\n"
    )
    code, out, err = live(jsort.main, ["angriest customer first", "--with-score"], text, monkeypatch)
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].split("\t", 1)[1].startswith("This is the third time")
    assert float(lines[0].split("\t")[0]) > float(lines[-1].split("\t")[0])


def test_jgrep_filters_by_meaning(monkeypatch):
    text = "INFO request served in 12ms\nERROR payment service failed: connection refused\nINFO cache warm\n"
    code, out, err = live(jgrep.main, ["a failure that needs attention", "-o"], text, monkeypatch)
    assert code == 0, err
    assert "ERROR payment" in out and "cache warm" not in out


def test_jpick_chooses_the_urgent_one(monkeypatch):
    text = "Weekly digest\nURGENT: production database is down\nLunch menu\n"
    code, out, _ = live(jpick.main, ["most urgent", "--why"], text, monkeypatch)
    assert code == 0 and out.startswith("URGENT: production database is down")


def test_jgate_exit_codes(monkeypatch):
    code, _, _ = live(
        jgate.main,
        ["mentions a security incident"],
        "We detected unauthorized access to the admin panel.\n",
        monkeypatch,
    )
    assert code == 0
    code, _, _ = live(jgate.main, ["mentions a security incident"], "The cafeteria has new sandwiches.\n", monkeypatch)
    assert code == 1


def test_jwatch_alerts_on_the_error(monkeypatch):
    text = "INFO fine\nERROR disk failure on nvme0, remounting read-only\nINFO fine again\n"
    code, out, _ = live(jwatch.main, ["something an operator should look at now"], text, monkeypatch)
    assert code == 0 and out.strip() == "ERROR disk failure on nvme0, remounting read-only"


def test_juniq_merges_paraphrases(monkeypatch):
    text = "Please add dark mode\nExport data as CSV\nA night theme would be great\n"
    code, out, _ = live(juniq.main, ["same underlying feature request", "-c"], text, monkeypatch)
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 2 and lines[0].strip().startswith("2 Please add dark mode")


def test_jhead_keeps_order(monkeypatch):
    text = "Agenda\nWe decided to ship on Friday\nCoffee was cold\nAgreed: Dana owns the rollout\nThanks all\n"
    code, out, _ = live(jhead.main, ["2", "a decision that was made"], text, monkeypatch)
    assert code == 0 and out.splitlines() == ["We decided to ship on Friday", "Agreed: Dana owns the rollout"]


def test_jtag_labels_and_scores(monkeypatch):
    text = "The app crashes when I open settings\nCould you add a dark theme?\n"
    code, out, _ = live(jtag.main, ["--labels", "bug,feature,question"], text, monkeypatch)
    assert code == 0 and out.splitlines()[0].startswith("bug\t") and out.splitlines()[1].startswith("feature\t")
    code, out, _ = live(
        jtag.main, ["--score", "how positive (0-100)"], "Absolutely love it!\nWorst purchase ever.\n", monkeypatch
    )
    a, b = (int(line.split("\t")[0]) for line in out.splitlines())
    assert a > 60 > b


def test_jroute_buckets(monkeypatch, tmp_path):
    text = "Can we get a quote for 50 seats?\nMy login is broken\nWIN A FREE CRUISE click now\n"
    code, out, err = live(
        jroute.main,
        ["sales:a sales lead", "support:a support request", "spam:junk", "-o", str(tmp_path), "--json"],
        text,
        monkeypatch,
    )
    assert code == 0, err
    buckets = [json.loads(line)["bucket"] for line in out.splitlines()]
    assert buckets == ["sales", "support", "spam"]


def test_jmatch_joins(monkeypatch, tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("Invoice #1042 Acme Corp $500\nInvoice #1043 Globex $1200\n")
    b.write_text("Payment received: Globex, 1200 USD\nPayment received: Acme Corporation, 500 USD\n")
    code, out, err = live(jmatch.main, [str(a), str(b), "the payment that settles the invoice"])
    assert code == 0, err
    lines = out.splitlines()
    assert "Acme" in lines[0] and lines[0].count("Acme") == 2 and "Globex" in lines[1]


def test_vercel_wire_when_that_backend_is_configured():
    from jevcore.backends import BACKENDS

    if not BACKENDS["vercel"].usable():
        pytest.skip("no Vercel AI Gateway key")
    creds = resolve("vercel")
    assert creds.backend.wire.name == "vercel"
    with httpx.Client(
        headers={"Authorization": f"Bearer {creds.key}", **creds.backend.wire.headers(creds.backend.model)}
    ) as c:
        r = c.post(
            creds.url,
            json=creds.backend.wire.body(
                creds.backend.model, "The sky is blue.", {"q": Noul("The text mentions a colour.")}
            ),
            timeout=30,
        )
    assert r.status_code == 200 and r.json()["answers"]["q"]["type"] == "boolean"
