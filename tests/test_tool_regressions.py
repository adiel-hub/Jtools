"""One test per defect an adversarial review of the tools found. Each fails on the old code.

Kept together rather than spread through the per-tool files so the list reads as what it is: the
things that were wrong, and the behaviour that replaced them.
"""

from __future__ import annotations

import json

import pytest

from jevcore.mock import POISON, MockJev
from jevtools._shared import unescape
from jevtools.jhead import main as jhead
from jevtools.jmatch import main as jmatch
from jevtools.jpick import main as jpick
from jevtools.jsort import main as jsort
from jevtools.jtag import main as jtag
from jevtools.juniq import main as juniq
from tests.conftest import write

TICKETS = "The app crashes on launch\nPlease add export to CSV\nHow do I reset my password?\n"


def test_juniq_never_folds_two_long_lines_for_free_on_a_shared_prefix(invoke, tmp_path):
    """The text juniq compares is already cut to --max-chars, so a cut line is never an exact repeat.

    Two different lines with a common prefix used to be declared byte-identical and one dropped
    with no call and no warning. The cut still limits what the model can see, so the verdict may
    well be "duplicate", but it is now a judgment rather than a coincidence of string length, and
    the run says which lines were compared on cut-down text.
    """
    same_prefix = "A" * 60
    f = write(tmp_path, "long.txt", f"{same_prefix} first tail\n{same_prefix} second tail\n")
    res = invoke(juniq, ["the same underlying request", "--max-chars", "60", f])
    assert res.mock.bodies, "the two lines were folded together without asking anything"
    assert "first 60 characters" in res.err, "nothing said the comparison ran on cut-down text"
    # Whatever the verdict, it is the model's: both lines reached it as candidates.
    assert any(b.get("state", {}).get("candidate", "").startswith("A" * 60) for b in res.mock.bodies)


def test_juniq_resolves_many_exact_repeats_without_rescanning_the_input(invoke):
    """The exact-repeat pass used to scan every record per duplicate: minutes of CPU, no calls."""
    res = invoke(juniq, ["the same request"], "the same identical line\n" * 4000)
    assert res.lines == ["the same identical line"] and not res.mock.bodies


def test_juniq_filters_that_print_nothing_report_no_match(invoke):
    """`juniq -d ... && alert` must not fire when there are no duplicates."""
    assert invoke(juniq, ["the same request", "-d"], "alpha one\nbeta two\n").code == 1
    assert invoke(juniq, ["the same request", "-u"], "one line\none line\n").code == 1
    assert invoke(juniq, ["the same request", "-d"], "one line\none line\n").code == 0


def test_jmatch_keeps_a_winner_when_another_group_call_fails(invoke, tmp_path):
    """jpick was fixed for this; jmatch re-added a failed group whole and then gave up on the line."""

    class GroupOfTwoIsDown(MockJev):
        def handle(self, body, headers):
            state = body.get("state")
            if isinstance(state, dict) and len(state.get("candidates") or []) == 2:
                return 503, {"error": {"message": "down"}}
            return super().handle(body, headers)

    a = write(tmp_path, "a.txt", "Acme invoice\n")
    b = write(tmp_path, "b.txt", "zzz one\nzzz two\nAcme invoice paid\n")
    res = invoke(
        jmatch, [a, b, "the same transaction", "--group", "2", "--unmatched"], mock_override=GroupOfTwoIsDown()
    )
    assert "Acme invoice paid" in res.out, f"a match another group found was discarded: {res.out!r}"


@pytest.mark.parametrize("fmt", ["{a}\t{b_line:03d}", "{a:{b}}", "{a}\t{score:.2f}"])
def test_jmatch_rejects_a_format_it_cannot_run_before_writing_anything(invoke, tmp_path, fmt):
    """Validation used to pass ints where the run passes strings, so it died halfway through."""
    a = write(tmp_path, "a.txt", "Acme invoice\nnothing here\n")
    b = write(tmp_path, "b.txt", "Acme invoice paid\n")
    res = invoke(jmatch, [a, b, "the same transaction", "--unmatched", "--format", fmt])
    assert res.code == 2 and res.out == ""
    assert "--format" in res.err and "Traceback" not in res.err


def test_jtag_json_stays_json_when_the_input_has_blank_lines(invoke):
    """--json is the machine-readable mode; a raw blank line stops jq at the second record."""
    res = invoke(jtag, ["--labels", "bug,feature", "--json"], "crash one\n\n   \ncrash two\n")
    for line in res.lines:
        obj = json.loads(line)  # each one must parse
        assert "line" in obj
    assert [json.loads(x).get("blank", False) for x in res.lines] == [False, True, True, False]


def test_jhead_json_rank_is_relevance_not_row_number(invoke):
    """`jq 'select(.rank==1)'` used to return the least relevant survivor."""
    text = "boring one\nboring two\nfurious worst outrage\n"
    rows = [json.loads(x) for x in invoke(jhead, ["2", "an angry customer", "--json"], text).lines]
    best = max(rows, key=lambda r: r["score"])
    assert best["rank"] == 1, f"rank 1 went to {min(rows, key=lambda r: r['rank'])['line']!r}"


@pytest.mark.parametrize("sep", [r"\u2192", "\\", r"C:\x", r"\N{BULLET}"])
def test_a_separator_python_cannot_decode_is_left_alone_rather_than_crashing(sep):
    """unescape used to round-trip through latin-1, which raises for anything above U+00FF."""
    assert unescape(sep) == sep


def test_the_escapes_a_shell_user_means_still_work():
    assert unescape(r"\t") == "\t" and unescape(r"\n") == "\n" and unescape(r"\\") == "\\"


@pytest.mark.parametrize(
    "tool,argv",
    [
        (jsort, ["most urgent"]),
        (jhead, ["2", "most urgent"]),
        (juniq, ["the same request"]),
        (jpick, ["the clearest one"]),
        (jtag, ["--labels", "bug,feature"]),
    ],
)
def test_a_file_that_cannot_be_read_is_a_usage_error_not_no_match(invoke, tool, argv):
    """`jsort urgent tikcets.txt || echo nothing urgent` must not report "nothing urgent"."""
    res = invoke(tool, [*argv, "/nonexistent/typo.txt"])
    assert res.code == 2, f"a missing file exited {res.code}"
    assert "typo.txt" in res.err


@pytest.mark.parametrize(
    "tool,argv",
    [
        (jsort, ["angriest"]),
        (jhead, ["1", "angriest"]),
        (juniq, ["the same request"]),
        (jtag, ["--labels", "bug,feature"]),
        (jpick, ["the clearest one"]),
    ],
)
def test_quiet_silences_the_end_of_run_notes_and_verbose_explains_each_decision(invoke, tool, argv):
    """Both flags are advertised on every tool's --help; in five of them they did nothing."""
    text = f"a furious customer writes\n{POISON} unjudgeable\n"
    loud = invoke(tool, argv, text)
    quiet = invoke(tool, [*argv, "-q"], text)
    assert loud.err.strip(), "nothing was printed to compare against"
    assert "could not be" not in quiet.err, f"-q left a run note on stderr: {quiet.err!r}"
    chatty = invoke(tool, [*argv, "--verbose"], text)
    assert "a furious customer writes" in chatty.err, "--verbose explained nothing"
