"""What each tool promises about its output as a set, independent of what Jev decides.

The rest of the suite checks that a tool makes the right decision about a line. These check the
shape of the answer: a filter must not invent lines, a sort must not lose them, a router must put
every line somewhere. They hold for any judgment, so they would catch a reordering, an off-by-one
in a cap, or a line silently dropped between reading and printing -- the failures that survive
tests written around one expected verdict.
"""

from __future__ import annotations

import json
import random

import pytest

from jevtools.jgrep import main as jgrep
from jevtools.jhead import main as jhead
from jevtools.jpick import main as jpick
from jevtools.jroute import main as jroute
from jevtools.jsort import main as jsort
from jevtools.jtag import main as jtag
from jevtools.juniq import main as juniq

WORDS = ["crash", "slow", "refund", "login", "export", "timeout", "invoice", "praise", "bug", "feature"]


@pytest.fixture
def lines() -> list[str]:
    """Forty tickets the mock scores unevenly, so no invariant is satisfied by an empty answer."""
    rng = random.Random(7)
    return [f"ticket {i:03d}: the {rng.choice(WORDS)} problem came back for a customer" for i in range(40)]


@pytest.fixture
def text(lines: list[str]) -> str:
    return "".join(line + "\n" for line in lines)


# ----------------------------------------------------------------------- jgrep: a filter


def test_jgrep_prints_a_subsequence_of_its_input(invoke, lines, text):
    kept = invoke(jgrep, ["the refund problem"], text).lines
    assert kept, "the sample was judged empty; the invariants below would be vacuous"
    assert [line for line in lines if line in set(kept)] == kept


def test_jgrep_and_invert_match_partition_the_input(invoke, lines, text):
    kept = invoke(jgrep, ["the refund problem"], text).lines
    dropped = invoke(jgrep, ["-v", "the refund problem"], text).lines
    assert not set(kept) & set(dropped), "a line was both a match and not a match"
    assert sorted(kept + dropped) == sorted(lines), "-v is not the complement of the same run"


def test_jgrep_count_is_the_number_of_lines_it_would_print(invoke, text):
    kept = invoke(jgrep, ["the refund problem"], text).lines
    assert invoke(jgrep, ["-c", "the refund problem"], text).lines == [str(len(kept))]


@pytest.mark.parametrize("m", [1, 3, 7])
def test_max_count_truncates_the_same_run_rather_than_changing_it(invoke, text, m):
    kept = invoke(jgrep, ["the refund problem"], text).lines
    capped = invoke(jgrep, ["-m", str(m), "the refund problem"], text).lines
    assert len(capped) <= m
    assert capped == kept[: len(capped)], "-m returned different lines, not the first few"


# ------------------------------------------------------------------- jsort: a reordering


def test_jsort_is_a_permutation_of_its_input(invoke, lines, text):
    assert sorted(invoke(jsort, ["most urgent"], text).lines) == sorted(lines)


@pytest.mark.parametrize("n", [1, 5, 40, 99])
def test_the_top_n_is_the_first_n_of_the_full_order(invoke, text, n):
    full = invoke(jsort, ["most urgent"], text).lines
    assert invoke(jsort, ["-n", str(n), "most urgent"], text).lines == full[: min(n, len(full))]


# --------------------------------------------------------- jhead: a cap that keeps order


@pytest.mark.parametrize("n", [1, 5, 40])
def test_jhead_prints_at_most_n_lines_in_input_order(invoke, lines, text, n):
    head = invoke(jhead, [str(n), "the key point"], text).lines
    assert len(head) <= n
    assert [line for line in lines if line in set(head)] == head


# ------------------------------------------------------------------ jtag: drops nothing


def test_jtag_prints_one_row_per_line_with_the_line_intact(invoke, lines, text):
    rows = invoke(jtag, ["--labels", "bug,feature"], text).lines
    assert len(rows) == len(lines)
    assert [row.split("\t", 1)[1] for row in rows] == lines, "a label column ate part of a line"


# ------------------------------------------------------------- jpick: exactly one line


def test_jpick_prints_exactly_one_line_and_it_came_from_the_input(invoke, lines, text):
    picked = invoke(jpick, ["the most urgent"], text).lines
    assert len(picked) == 1
    assert picked[0] in lines


# --------------------------------------------------------------- juniq: groups conserve


def test_juniq_groups_account_for_every_input_line_exactly_once(invoke, lines, text):
    rows = [json.loads(line) for line in invoke(juniq, ["--json", "the same problem"], text).lines]
    assert all(row["line"] in lines for row in rows), "juniq printed a line that was not in the input"
    assert len({row["line"] for row in rows}) == len(rows), "a line was kept twice"
    assert sum(row["count"] for row in rows) == len(lines), "the group sizes do not add up to the input"


# ------------------------------------------------------- jroute: every line lands once


def test_jroute_puts_every_line_in_exactly_one_bucket(invoke, lines, text, tmp_path):
    out = tmp_path / "routed"
    res = invoke(
        jroute,
        ["bug:a bug report", "other:anything else", "--default", "unrouted", "-o", str(out)],
        text,
    )
    assert res.code in (0, 5), res.err
    landed = [line for f in sorted(out.glob("*.txt")) for line in f.read_text().splitlines()]
    assert sorted(landed) == sorted(lines), "a line was routed twice, or nowhere"


def test_a_tool_that_cannot_stream_says_so_before_it_spends_the_memory(invoke, monkeypatch):
    """jsort and friends hold every record at once; above a point that is worth a sentence.

    The warning is issued after reading and before judging, which is where a user can still do
    something about it: the default budget stops a run near 77,000 lines, so an input large enough
    to matter has already had `--budget 0` applied to it.
    """
    from jevtools import _shared

    monkeypatch.setattr(_shared, "LARGE_INPUT", 20)
    res = invoke(jsort, ["most urgent"], "".join(f"line {i}: a condition\n" for i in range(25)))
    assert res.code == 0
    assert "25 records held in memory at once" in res.err
    assert len(res.lines) == 25, "the warning replaced the answer"


def test_an_ordinary_input_is_not_warned_about(invoke, text):
    res = invoke(jsort, ["most urgent"], text)
    assert "held in memory" not in res.err
