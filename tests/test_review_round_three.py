"""One test per defect the third and fourth adversarial reviews found.

The theme of this round is that a tool must never answer for input it did not actually judge, and
that a line of input is data, never code.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import subprocess
import sys
import time

import httpx
import pytest

from jevcore.errors import UsageError
from jevcore.inputs import iter_records
from jevcore.mock import POISON, MockJev, serve
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
    """--stdout was an elif after --json, so --json printed every bucket regardless.

    The files still hold every record, which is what makes selecting on stdout safe here.
    """
    inbox = "Can we get a quote for 50 seats?\nFREE PRIZE click here\n"
    out = tmp_path / "buckets"
    res = invoke(jroute, ["sales:a sales lead", "spam:junk", "-o", str(out), "--json", "--stdout", "sales"], inbox)
    assert res.lines and all('"bucket": "sales"' in line for line in res.lines)
    assert sum(len(p.read_text().splitlines()) for p in out.glob("*.txt")) == 2, "a record went nowhere"


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
    records = list(iter_records([str(f)], structured="csv", field="id"))
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


# --------------------------------------------------------------- the fourth review


@pytest.mark.parametrize("command", ["", "   "])
def test_an_empty_exec_is_refused(command):
    """An empty command becomes just `"$1"`, which makes the watched line the program that runs."""
    with pytest.raises(UsageError, match="empty one would run"):
        prepare_exec(command)


@pytest.mark.parametrize("command", ["printf '%s' 'api: {} ' >> f", "printf '%s' 'api: {}' >> f", 'echo "x {} y"'])
def test_a_placeholder_inside_quotes_is_refused_however_it_is_spaced(command):
    """Looking at the neighbouring characters cannot tell `'{} '` from `'{}'`; a quote scan can."""
    with pytest.raises(UsageError, match="already quoted"):
        prepare_exec(command)


@pytest.mark.parametrize(
    "argument,expected",
    [("stdin", (0, 1)), ("dash", (0, 1)), ("a readable file", (0, 1)), ("a directory", (2,))],
    ids=["no files", "-", "a readable file", "a directory"],
)
def test_the_gates_up_front_file_check_takes_the_awkward_arguments(invoke, tmp_path, argument, expected):
    """Opening every file first must not break the ordinary ways of naming one, or not naming any."""
    good = write(tmp_path, "ok.log", "an error happened here\n")
    (tmp_path / "adir").mkdir()
    files = {"stdin": [], "dash": ["-"], "a readable file": [good], "a directory": [str(tmp_path / "adir")]}
    res = invoke(jgate, ["an error", *files[argument]], "an error happened here\n")
    assert res.code in expected, f"{argument} exited {res.code}: {res.err!r}"


def test_a_gate_checks_its_files_before_it_stops_early(invoke, tmp_path):
    """--each stops at the first fitting line, so a later missing file was never even opened."""
    big = write(tmp_path, "big.log", "ERROR payment service failed\n" * 500)
    res = invoke(jgate, ["an error", "--each", big, str(tmp_path / "typo.log")])
    assert res.code == 2, f"exited {res.code} having never opened a file it was given"
    assert "typo.log" in res.err


def test_a_nul_byte_in_a_line_does_not_lose_the_alert(tmp_path, monkeypatch):
    """create_subprocess_exec rejects a NUL; that must not silently drop the alert."""
    monkeypatch.chdir(tmp_path)
    seen = tmp_path / "seen.txt"
    monkeypatch.setattr(sys, "stdin", io.StringIO("an error \x00 happened\nan error again\n"))
    code = jwatch(
        ["an error", "--exec", f"printf '%s\\n' {{}} >> {seen}", "--max", "2"],
        transport=httpx.MockTransport(MockJev()),
        out=io.StringIO(),
        err=io.StringIO(),
    )
    time.sleep(0.6)
    assert code in (0, 1, 5)
    written = seen.read_text().splitlines() if seen.exists() else []
    assert len(written) == 2, f"an alert was lost: {written}"


def test_a_cache_written_by_an_earlier_version_is_not_kept_and_claimed(tmp_path):
    """Its rows carry no record of which model produced them, so they can never be reconciled."""
    import sqlite3

    from jevcore.cache import DiskCache, cache_key
    from jevcore.questions import Noul

    path = tmp_path / "answers.sqlite"
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("CREATE TABLE answers (key TEXT PRIMARY KEY, answer TEXT NOT NULL, at REAL NOT NULL) WITHOUT ROWID")
    key = cache_key("jev-latest", "a line", Noul("a question"))
    db.execute("INSERT INTO answers VALUES (?, ?, 0)", (key, '{"type": "noul", "p": 0.9}'))
    db.close()

    cache = DiskCache(path)
    try:
        assert cache.get(key) is None, "a row with no provenance survived the upgrade"
    finally:
        cache.close()


def test_a_moved_alias_keeps_the_answers_the_new_version_gave(tmp_path):
    """Under -j 20 a sibling call may already have stored an answer from the new version."""
    from jevcore.cache import DiskCache
    from jevcore.questions import NoulAnswer

    cache = DiskCache(tmp_path / "answers.sqlite")
    try:
        cache.reconcile("jev-latest", "jev-1.0")
        cache.put("old", NoulAnswer(0.9), "jev-latest", "jev-1.0")
        cache.put("new", NoulAnswer(0.9), "jev-latest", "jev-2.0")
        assert cache.reconcile("jev-latest", "jev-2.0") == "jev-1.0"
        assert cache.get("old") is None, "the retired version's answer was kept"
        assert cache.get("new") is not None, "an answer from the new version was thrown away"
    finally:
        cache.close()


def test_a_byte_order_mark_is_stripped_from_standard_input_too(tmp_path):
    """A pipe is the commonest way in, and it is where the spreadsheet case actually shows up."""
    csv_text = "﻿id,text\n1,alpha\n"
    done = subprocess.run(
        [sys.executable, "-m", "jevtools.jgrep", "--csv", "--field", "id", "--dry-run", "anything", "-"],
        input=csv_text,
        capture_output=True,
        text=True,
        env={
            **{k: v for k, v in os.environ.items() if not k.endswith("API_KEY")},
            "HOME": str(tmp_path),
            "TYPESAFE_API_KEY": "k",
            "PYTHONPATH": str(pathlib.Path(__file__).resolve().parent.parent / "src"),
        },
        check=False,
    )
    assert done.returncode == 0, done.stderr[-400:]
    assert "no column" not in done.stderr, done.stderr


def test_a_pattern_does_not_match_above_the_directory_being_searched(tmp_path, monkeypatch):
    """An absolute argument used to expose its parent directories to --exclude and --glob."""
    from jevcore.inputs import discover

    root = tmp_path / "fixture-tree" / "proj"
    root.mkdir(parents=True)
    (root / "keep.py").write_text("code\n")
    monkeypatch.chdir(root)
    relative, _ = discover(["."], recursive=True, excludes=["*fixture*"])
    absolute, _ = discover([str(root)], recursive=True, excludes=["*fixture*"])
    assert [os.path.basename(f) for f in relative] == ["keep.py"]
    assert [os.path.basename(f) for f in absolute] == ["keep.py"], "the path above the root was matched"


def test_narrowing_flags_narrow_the_same_way_in_both_output_modes(invoke, tmp_path):
    """--stdout and --no-files both keep less on purpose; --json must not change which is kept.

    An earlier attempt refused the three together as lossy, which named the wrong flag: --no-files
    with --stdout loses exactly as much without --json, and that combination is the documented
    `tail -f | jroute … --stdout alert | notify` recipe.
    """
    inbox = "Can we get a quote for 50 seats?\nFREE PRIZE click here\n"
    plain = invoke(jroute, ["sales:a sales lead", "spam:junk", "--no-files", "--stdout", "sales"], inbox)
    tagged = invoke(jroute, ["sales:a sales lead", "spam:junk", "--no-files", "--json", "--stdout", "sales"], inbox)
    assert plain.code == 0 and tagged.code == 0
    assert len(plain.lines) == len(tagged.lines) == 1
    assert json.loads(tagged.lines[0])["bucket"] == "sales"


# --------------------------------------------------------------- the fifth review


@pytest.mark.parametrize(
    "command",
    [r'echo "\"{}\""', "{}", "$1", "  {}  ", '"$1"'],
    ids=["escaped quotes inside quotes", "only the placeholder", "only $1", "padded placeholder", "only the parameter"],
)
def test_the_line_can_never_become_the_command_or_lose_its_quoting(command):
    """Two ways the guard was got round: an escaped quote, and a command that is just the line."""
    with pytest.raises(UsageError):
        prepare_exec(command)


@pytest.mark.parametrize("command", [r"echo \" {}", "notify-send {}", "logger -t api {} && true"])
def test_a_backslash_outside_quotes_is_not_a_quote(command):
    """The scan must not reject a command that is fine: `\\" {}` has the placeholder in the open."""
    assert prepare_exec(command).endswith(('"$1"', '"$1" && true'))


@pytest.mark.timeout(60)
def test_the_gate_does_not_consume_a_named_pipe_checking_it(tmp_path):
    """Opening a FIFO blocks for a writer and closing it kills that writer, losing the input."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    feeder = subprocess.Popen(f"printf 'ERROR disk failed\\n' > {fifo}", shell=True)
    try:
        server = serve(MockJev())
        try:
            done = subprocess.run(
                [sys.executable, "-m", "jevtools.jgate", "--each", "an error", str(fifo)],
                capture_output=True,
                text=True,
                env=env_for_gate(server.url, tmp_path),
                timeout=40,
            )
        finally:
            server.shutdown()
        assert done.returncode in (0, 1), done.stderr[-400:]
    finally:
        feeder.wait(timeout=10)


def env_for_gate(url: str, tmp_path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("JEV_")}
    env.pop("vercel_api_key", None)
    env |= {
        "JEV_GATEWAY_URL": url,
        "JEV_GATEWAY_API_KEY": "t",
        "JEV_API": "gateway",
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "PYTHONPATH": str(pathlib.Path(__file__).resolve().parent.parent / "src"),
    }
    return env


def test_doctor_reports_an_unusable_environment_value_rather_than_crashing(tmp_path):
    """jtools has its own entry point, so it has to map a usage error itself."""
    env = {k: v for k, v in os.environ.items() if not k.endswith("API_KEY")}
    env.pop("vercel_api_key", None)
    env |= {
        "HOME": str(tmp_path),
        "TYPESAFE_API_KEY": "k",
        "JEV_PRICE_PER_MTOK": "abc",
        "PYTHONPATH": str(pathlib.Path(__file__).resolve().parent.parent / "src"),
    }
    done = subprocess.run(
        [sys.executable, "-m", "jevtools.jtools", "doctor"], capture_output=True, text=True, env=env, timeout=60
    )
    assert done.returncode == 2, done.stdout[-300:]
    assert "Traceback" not in done.stderr and "JEV_PRICE_PER_MTOK" in done.stderr


def test_help_and_a_library_client_both_honour_the_environment(tmp_path):
    """The defaults moved out of import time; --help must still print the number in effect."""
    env = {k: v for k, v in os.environ.items() if not k.endswith("API_KEY")}
    env.pop("vercel_api_key", None)
    env |= {
        "HOME": str(tmp_path),
        "TYPESAFE_API_KEY": "k",
        "JEV_TIMEOUT": "60",
        "JEV_CONCURRENCY": "3",
        "PYTHONPATH": str(pathlib.Path(__file__).resolve().parent.parent / "src"),
    }
    done = subprocess.run(
        [sys.executable, "-m", "jevtools.jgrep", "--help"], capture_output=True, text=True, env=env, timeout=60
    )
    assert "default 3, or $JEV_CONCURRENCY" in done.stdout, done.stdout
    assert "default 60" in done.stdout

    code = "from jevcore.auth import resolve; from jevcore.client import Jev; "
    code += "j = Jev(resolve(None), disk_cache=False); print(j.timeout, j.concurrency)"
    built = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert built.stdout.split() == ["60.0", "3"], built.stdout + built.stderr[-300:]


def test_an_absolute_pattern_matches_an_absolute_path_and_nothing_above_the_root(tmp_path, monkeypatch):
    """Both directions: an absolute --glob has to work, and --exclude must not see the root's parents."""
    from jevcore.inputs import discover

    root = tmp_path / "tests" / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("code\n")
    monkeypatch.chdir(tmp_path)

    kept, _ = discover([str(root)], recursive=True, excludes=["tests/*"])
    assert [os.path.basename(f) for f in kept] == ["a.py"], "a directory above the root was matched"
    found, _ = discover([str(root)], recursive=True, globs=[f"{root}/src/*.py"])
    assert [os.path.basename(f) for f in found] == ["a.py"], "an absolute pattern matched nothing"
    assert discover([str(root)], recursive=True, globs=["src/*.py"])[0] == found


# --------------------------------------------------------------- the sixth review


def test_a_socket_argument_is_still_caught_by_the_gate(invoke, tmp_path):
    """Skipping the open for a FIFO must not skip it for everything that is not a regular file."""
    import socket

    good = write(tmp_path, "a.log", "an error happened here\n")
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(tmp_path / "b.sock"))
    try:
        res = invoke(jgate, ["an error", "--each", good, str(tmp_path / "b.sock")])
        assert res.code == 2, f"exited {res.code} for an argument it cannot read"
        assert "b.sock" in res.err
    finally:
        sock.close()


@pytest.mark.parametrize("pattern,kind", [("*/tests/*", "exclude"), ("*/src/*.py", "glob")])
def test_a_pattern_with_a_path_in_it_works_however_the_root_is_spelled(tmp_path, monkeypatch, pattern, kind):
    """The same command must not depend on whether the directory was named relatively."""
    from jevcore.inputs import discover

    (tmp_path / "proj" / "tests").mkdir(parents=True)
    (tmp_path / "proj" / "src").mkdir()
    (tmp_path / "proj" / "tests" / "t.py").write_text("secret\n")
    (tmp_path / "proj" / "src" / "a.py").write_text("source\n")
    monkeypatch.chdir(tmp_path)

    kwargs = {"excludes": [pattern]} if kind == "exclude" else {"globs": [pattern]}
    relative, _ = discover(["proj"], recursive=True, **kwargs)
    absolute, _ = discover([str(tmp_path / "proj")], recursive=True, **kwargs)
    assert [os.path.basename(f) for f in relative] == ["a.py"]
    assert [os.path.basename(f) for f in absolute] == ["a.py"], "the absolute spelling behaved differently"


def test_help_survives_a_number_too_large_to_be_a_float(tmp_path):
    """--help reads the environment now, so every unusable value has to fall back, not raise."""
    env = {k: v for k, v in os.environ.items() if not k.endswith("API_KEY")}
    env.pop("vercel_api_key", None)
    env |= {
        "HOME": str(tmp_path),
        "TYPESAFE_API_KEY": "k",
        "JEV_CONCURRENCY": "1" + "0" * 400,
        "PYTHONPATH": str(pathlib.Path(__file__).resolve().parent.parent / "src"),
    }
    done = subprocess.run(
        [sys.executable, "-m", "jevtools.jgrep", "--help"], capture_output=True, text=True, env=env, timeout=60
    )
    assert done.returncode == 0, done.stderr[-400:]
    assert "Traceback" not in done.stderr
    assert "requests in flight" in done.stdout


def test_each_environment_variable_is_read_only_for_the_value_it_sets(monkeypatch, creds):
    """Supplying one value must exempt that variable, not require supplying the other as well."""
    from jevcore.client import Jev

    monkeypatch.setenv("JEV_TIMEOUT", "bogus")
    assert Jev(creds, timeout=30.0, disk_cache=False).timeout == 30.0
    monkeypatch.delenv("JEV_TIMEOUT")
    monkeypatch.setenv("JEV_CONCURRENCY", "bogus")
    assert Jev(creds, concurrency=4, disk_cache=False).concurrency == 4


@pytest.mark.parametrize(
    "command,reason",
    [(r"echo \{}", "unterminated"), ("notify 'hi", "unclosed quote"), ('notify "hi', "unclosed quote")],
)
def test_a_command_the_substitution_would_break_is_named_for_what_is_wrong(command, reason):
    """An unterminated quote used to be reported as a quoted placeholder the user never wrote."""
    with pytest.raises(UsageError) as caught:
        prepare_exec(command)
    assert reason in str(caught.value), str(caught.value)


# ------------------------------------------------------------- the seventh review


@pytest.mark.parametrize("command", ['notify-send "alert" "${1}"', 'echo "$@"', 'echo "$*"', "echo $1"])
def test_a_command_that_names_the_line_itself_is_not_given_a_second_copy(command):
    """`"${1}"` is how a shell user spells it; looking for the two characters `$1` does not see it."""
    assert prepare_exec(command) == command, "the line would be passed twice"


@pytest.mark.parametrize("command", ["${1}", '"${1}"', "$@", '"$@"', "$1", '"$1"', "{}"])
def test_a_command_that_is_only_the_line_is_refused_however_it_is_spelled(command):
    with pytest.raises(UsageError, match="cannot be only the line"):
        prepare_exec(command)


def test_a_character_device_is_a_readable_argument(invoke, tmp_path):
    """The readability probe must accept /dev/null; only a FIFO is skipped, everything else opens."""
    good = write(tmp_path, "a.log", "an error happened here\n")
    res = invoke(jgate, ["an error", good, "/dev/null"])
    assert res.code == 0, f"exited {res.code}: {res.err}"
    assert "/dev/null" not in res.err


@pytest.mark.parametrize("spec", [".", "..", "..."])
def test_a_bucket_that_names_a_directory_is_refused_before_anything_is_written(invoke, tmp_path, spec):
    """`.` and `..` pass the character check and then name the directory itself.

    Before this they were found out at the first write -- by which time the other buckets had
    already been created and filled, so the run half happened and then exited 2.
    """
    res = invoke(jroute, [f"{spec}:junk", "ok:anything else", "-o", str(tmp_path / "out"), "--ext", ""])
    assert res.code == 2
    assert "directory, not a name" in res.err
    assert not (tmp_path / "out").exists(), "a refused run created its output directory"


@pytest.mark.parametrize("spec", [".", ".."])
def test_the_same_holds_for_the_default_bucket(invoke, tmp_path, spec):
    res = invoke(jroute, ["a:one thing", "b:another", "--default", spec, "-o", str(tmp_path / "out"), "--ext", ""])
    assert res.code == 2
    assert "directory, not a name" in res.err
