"""One test per defect the third and fourth adversarial reviews found.

The theme of this round is that a tool must never answer for input it did not actually judge, and
that a line of input is data, never code.
"""

from __future__ import annotations

import io
import os
import pathlib
import subprocess
import sys
import time

import httpx
import pytest

from jevcore.errors import UsageError
from jevcore.inputs import iter_records
from jevcore.mock import POISON, MockJev
from jevtools.jgate import main as jgate
from jevtools.jmatch import main as jmatch
from jevtools.jpick import main as jpick
from jevtools.jroute import main as jroute
from jevtools.jsort import main as jsort
from jevtools.jtag import main as jtag
from jevtools.jwatch import main as jwatch
from jevtools.jwatch import prepare_exec
from tests.conftest import write

LINES = "The app crashes on launch\nPlease add export to CSV\nHow do I reset my password?\n"


# --------------------------------------------------------------------- jwatch --exec


@pytest.mark.parametrize(
    "command",
    [
        'printf "%s\\n" "{}" >> hits.txt',
        "printf '%s\\n' '{}' >> hits.txt",
        'sh -c "echo {}"',
    ],
)
def test_a_quoted_placeholder_is_refused_rather_than_mangled(command):
    """A user-quoted {} would expand to ""$1"", where the line is no longer quoted and splits."""
    with pytest.raises(UsageError, match="already quoted"):
        prepare_exec(command)


@pytest.mark.parametrize(
    "command,expected",
    [
        ("notify-send {}", 'notify-send "$1"'),
        ("logger -t api", 'logger -t api "$1"'),  # no placeholder: appended
        ('notify-send "api: $1"', 'notify-send "api: $1"'),  # $1 written directly: left alone
    ],
)
def test_the_accepted_exec_spellings(command, expected):
    assert prepare_exec(command) == expected


@pytest.mark.timeout(60)
def test_a_hostile_log_line_is_never_executed(tmp_path, monkeypatch):
    """`tail -f app.log | jwatch … --exec` means the line comes from whoever writes the log."""
    monkeypatch.chdir(tmp_path)
    canary = tmp_path / "PWNED"
    line = f"error $(touch {canary}) `touch {canary}2`; touch {canary}3"
    monkeypatch.setattr(sys, "stdin", io.StringIO(line + "\n"))
    out, err = io.StringIO(), io.StringIO()
    code = jwatch(
        ["an error", "--exec", f"printf '%s' {{}} >> {tmp_path / 'seen.txt'}", "--max", "1"],
        transport=httpx.MockTransport(MockJev()),
        out=out,
        err=err,
    )
    time.sleep(0.5)
    made = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("PWNED"))
    assert not made, f"the log line ran as code: {made}"
    assert code in (0, 1, 5)
    assert (tmp_path / "seen.txt").read_text() == line, "the command did not receive the line intact"


def test_jwatch_reports_an_unreadable_file_as_a_usage_error(invoke):
    """`jwatch "outage" typo.log || echo "no alerts"` must not report "no alerts" for a typo."""
    assert invoke(jwatch, ["an outage", "/nonexistent/typo.log"]).code == 2


# --------------------------------------------------------------------- jgate


@pytest.mark.parametrize("extra", [[], ["--all"], ["--each"], ["-P"], ["--json"]])
def test_a_gate_does_not_pass_for_a_file_it_could_not_read(invoke, tmp_path, extra):
    """`jgate --all "safe" *.log && deploy` must not deploy because one file was missing."""
    good = write(tmp_path, "ok.log", "everything is completely fine and calm here\n")
    res = invoke(jgate, ["something worth a look", good, "/nonexistent/gone.log", *extra])
    assert res.code == 2, f"jgate {extra} exited {res.code} for an unreadable file"


def test_strict_fails_closed_in_each_mode_too(invoke):
    """--strict is documented as "stop with exit 4 on the first API error"; --each ignored it."""
    text = f"{POISON} nobody can judge this\nan error happened\n"
    assert invoke(jgate, ["an error", "--each", "--strict", "-j", "1"], text).code == 4
    assert invoke(jgate, ["an error", "--all", "--strict", "-j", "1"], text).code == 4


def test_a_record_judged_on_a_prefix_is_reported(invoke):
    """A gate that says "no" about the first 8,000 characters should say that is what it saw."""
    res = invoke(jgate, ["an error", "--each", "--max-chars", "40"], "x" * 60 + " ERROR payment failed\n")
    assert "first 40 characters" in res.err


def test_a_gate_keeps_its_verdict_when_stdout_closes(monkeypatch):
    """Elsewhere a closed pipe means the reader has what it wanted; for a gate it must not pass."""

    class Closed(io.StringIO):
        def write(self, text: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(sys, "stdin", io.StringIO("everything is completely fine and calm\n"))
    code = jgate(
        ["a security incident", "--json"], transport=httpx.MockTransport(MockJev()), out=Closed(), err=io.StringIO()
    )
    assert code == 1, f"a failing gate exited {code} because its reader went away"


# --------------------------------------------------------------------- jpick, jroute


@pytest.mark.parametrize("n", [3, 5, 6, 7, 9, 13])
def test_jpick_group_two_does_not_crash_on_an_odd_count(invoke, n):
    """A balanced split leaves a group of one, and a choice question needs two options."""
    text = "".join(f"candidate number {i} about a crash\n" for i in range(n))
    res = invoke(jpick, ["the clearest bug report", "--group", "2"], text)
    assert res.code == 0 and len(res.lines) == 1
    assert "Traceback" not in res.err


def test_truncate_does_not_empty_the_buckets_of_a_run_that_routes_nothing(invoke, tmp_path):
    """An upstream failure produces no lines; it must not also destroy yesterday's output."""
    out = tmp_path / "buckets"
    out.mkdir()
    (out / "sales.txt").write_text("yesterday's leads\n")
    res = invoke(jroute, ["sales:a sales lead", "spam:junk", "-o", str(out), "--truncate"], "")
    assert res.code == 1
    assert (out / "sales.txt").read_text() == "yesterday's leads\n", "an empty run wiped the buckets"
    # A run that does route something still starts the files empty.
    invoke(jroute, ["sales:a sales lead", "spam:junk", "-o", str(out), "--truncate"], "Pricing for the team plan?\n")
    assert (out / "sales.txt").read_text() == "Pricing for the team plan?\n"


def test_jroute_refuses_a_threshold_it_could_not_act_on(invoke, tmp_path):
    """-p only ever mattered with --default, so on its own it silently did nothing."""
    res = invoke(jroute, ["sales:a lead", "spam:junk", "-o", str(tmp_path), "-p", "0.99"], "Pricing?\n")
    assert res.code == 2 and "--default" in res.err
    ok = invoke(jroute, ["sales:a lead", "spam:junk", "-o", str(tmp_path), "-p", "0.99", "--default", "other"], "x\n")
    assert ok.code == 0


def test_stdout_selects_what_reaches_stdout_under_json_as_well(invoke, tmp_path):
    """--stdout was an elif after --json, so --json printed every bucket regardless."""
    inbox = "Can we get a quote for 50 seats?\nFREE PRIZE click here\n"
    res = invoke(jroute, ["sales:a sales lead", "spam:junk", "--no-files", "--json", "--stdout", "sales"], inbox)
    assert res.lines and all('"bucket": "sales"' in line for line in res.lines)


# --------------------------------------------------------------------- inputs


def test_a_bare_carriage_return_does_not_start_a_new_record(tmp_path):
    """Progress-bar output and some container logs are full of them; one line stays one line."""
    f = tmp_path / "cr.log"
    f.write_bytes(b"progress 50%\rERROR payment failed\nsecond line\n")
    records = list(iter_records([str(f)]))
    assert [r.lineno for r in records] == [1, 2]
    assert records[0].text.endswith("ERROR payment failed")


def test_a_byte_order_mark_does_not_hide_the_first_csv_column(tmp_path):
    """Every CSV a spreadsheet writes starts with one, so --field <first column> never worked."""
    f = tmp_path / "x.csv"
    f.write_bytes("﻿id,text\n1,alpha\n".encode())
    records = list(iter_records([str(f)], csv_field="id"))
    assert [r.text for r in records] == ["1"]


# --------------------------------------------------------------------- the rest


@pytest.mark.parametrize("fmt", ["{a}\t{b_line:03d}", "{a}\t{b}\t{score}"])
def test_jmatch_accepts_a_format_that_works_for_the_rows_this_run_prints(invoke, tmp_path, fmt):
    """The unmatched row is only printed with --unmatched, and never under --json."""
    a = write(tmp_path, "a.txt", "Acme invoice\n")
    b = write(tmp_path, "b.txt", "Acme invoice paid\n")
    assert invoke(jmatch, [a, b, "the same transaction", "--format", fmt]).code == 0


def test_jmatch_still_rejects_a_format_the_unmatched_row_would_break(invoke, tmp_path):
    a = write(tmp_path, "a.txt", "Acme invoice\nnothing at all like it\n")
    b = write(tmp_path, "b.txt", "Acme invoice paid\n")
    res = invoke(jmatch, [a, b, "the same transaction", "--unmatched", "--format", "{a}\t{b_line:03d}"])
    assert res.code == 2 and "--format" in res.err


def test_a_blank_line_is_not_an_unjudged_line_in_json(invoke):
    """`jq 'select(.judged == false)'` must keep meaning "this line could not be judged"."""
    import json

    rows = [json.loads(x) for x in invoke(jtag, ["--labels", "bug,feature", "--json"], f"crash\n\n{POISON} x\n").lines]
    blank = next(r for r in rows if r.get("blank"))
    unjudged = next(r for r in rows if r.get("judged") is False)
    assert "judged" not in blank and "source" in blank and "lineno" in blank
    assert not unjudged.get("blank")


def test_a_count_of_unreadable_records_is_not_hidden_by_quiet(invoke, tmp_path):
    """-q hides end-of-run notes; records that could not be read are a problem, not a note."""
    from jevtools.jgrep import main as jgrep

    broken = write(tmp_path, "x.jsonl", "".join("not json\n" for _ in range(15)) + '{"text":"a crash"}\n')
    quiet = invoke(jgrep, ["a crash report", "--jsonl", "--field", "text", "-q", broken])
    assert "more input errors" in quiet.err, f"-q hid how many records could not be read: {quiet.err!r}"


def test_verbose_prints_each_decision_as_it_lands(monkeypatch, tmp_path):
    """The shared help says "as it is made"; five tools only printed after every answer was in."""

    class Stamped(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self.start = time.monotonic()
            self.at: list[float] = []

        def write(self, text: str) -> int:
            if text.strip():
                self.at.append(time.monotonic() - self.start)
            return super().write(text)

    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"line {i} is furious\n" for i in range(4))))
    err = Stamped()
    jsort(["angriest", "-v", "-j", "1"], transport=httpx.MockTransport(MockJev(delay=0.1)), out=io.StringIO(), err=err)
    assert len(err.at) >= 4
    assert err.at[0] < err.at[-1] - 0.15, f"every line appeared at once: {[round(t, 2) for t in err.at]}"


def test_a_dry_run_hides_a_token_in_the_url_userinfo(tmp_path):
    """Some gateways carry the key before the host rather than in the query string."""
    env = {k: v for k, v in os.environ.items() if not k.endswith("API_KEY")}
    env.pop("vercel_api_key", None)
    env |= {
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "JEV_API": "gateway",
        "JEV_GATEWAY_URL": "https://sk-SUPERSECRET123@gw.example.com/v1/systemone",
        "JEV_GATEWAY_API_KEY": "k",
        "PYTHONPATH": str(pathlib.Path(__file__).resolve().parent.parent / "src"),
    }
    done = subprocess.run(
        [sys.executable, "-m", "jevtools.jgrep", "anything", "--dry-run"],
        input="a line\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-400:]
    assert "SUPERSECRET123" not in done.stdout + done.stderr
    assert "gw.example.com" in done.stdout


def test_a_repeated_file_argument_is_counted_once_per_occurrence(invoke, tmp_path):
    """`grep -c pat a.txt a.txt` prints the file's own count twice, not the sum twice."""
    f = write(tmp_path, "a.txt", "The app crashes on launch\nanother crash report here\ncalm and fine\n")
    from jevtools.jgrep import main as jgrep

    one = invoke(jgrep, ["-c", "a crash report", f])
    two = invoke(jgrep, ["-c", "a crash report", f, f])
    n = int(one.lines[0])
    assert n >= 1
    assert [line.split(":")[-1] for line in two.lines] == [str(n), str(n)], two.lines


def test_a_path_shaped_glob_is_read_relative_to_the_directory_searched(tmp_path, monkeypatch):
    """`jgrep -r --glob 'src/*.py' proj` means "the Python files in proj/src"."""
    from jevcore.inputs import discover

    (tmp_path / "proj" / "src").mkdir(parents=True)
    (tmp_path / "proj" / "docs").mkdir()
    (tmp_path / "proj" / "src" / "a.py").write_text("code\n")
    (tmp_path / "proj" / "docs" / "b.py").write_text("docs\n")
    monkeypatch.chdir(tmp_path)
    found, errors = discover(["proj"], recursive=True, globs=["src/*.py"])
    assert not errors
    assert [os.path.basename(f) for f in found] == ["a.py"], found
    # The working-directory spelling still works, as does a bare name.
    assert discover(["proj"], recursive=True, globs=["proj/src/*.py"])[0] == found
    assert len(discover(["proj"], recursive=True, globs=["*.py"])[0]) == 2


def test_doctor_does_not_print_a_token_carried_in_the_endpoint(tmp_path):
    """Doctor output is the first thing anyone pastes into a bug report."""
    from jevcore.mock import serve

    server = serve(MockJev())
    try:
        env = {k: v for k, v in os.environ.items() if not k.endswith("API_KEY")}
        env.pop("vercel_api_key", None)
        env |= {
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "JEV_API": "gateway",
            "JEV_GATEWAY_URL": server.url + "?token=SUPERSECRET123",
            "JEV_GATEWAY_API_KEY": "abcdefghijklmnop",
            "PYTHONPATH": str(pathlib.Path(__file__).resolve().parent.parent / "src"),
        }
        done = subprocess.run(
            [sys.executable, "-m", "jevtools.jtools", "doctor"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
        assert "SUPERSECRET123" not in done.stdout + done.stderr, done.stdout
        assert "abcdefghijklmnop" not in done.stdout, "the key was printed in full"
        assert "127.0.0.1" in done.stdout
    finally:
        server.shutdown()
