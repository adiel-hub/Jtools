"""juniq (pairwise noul), jtag (choice/score column), jroute (choice into files)."""

import json

import pytest

from jevcore.mock import POISON, MockJev
from jevtools.jroute import main as jroute
from jevtools.jtag import main as jtag
from jevtools.juniq import main as juniq
from tests.conftest import write

REQUESTS = (
    "Please add dark mode to the app\n"
    "Export data as CSV\n"
    "dark mode for the app please add\n"
    "Would love a CSV export of data\n"
    "Fix the login crash\n"
    "Please add dark mode to the app\n"
)


def test_juniq_keeps_first_occurrence(invoke, mock):
    res = invoke(juniq, ["same underlying request"], REQUESTS)
    assert res.code == 0
    assert res.lines == ["Please add dark mode to the app", "Export data as CSV", "Fix the login crash"]
    # the exact repeat (line 6) costs nothing; 5 unique lines, the first has no window -> 4 calls
    assert len(mock.bodies) == 4
    body = mock.bodies[0]
    assert set(body["state"]) == {"candidate", "kept"} and list(body["questions"]) == ["k0"]


def test_juniq_counts_groups_repeated_unique(invoke):
    res = invoke(juniq, ["same", "-c"], REQUESTS)
    assert res.lines == [
        "      3 Please add dark mode to the app",
        "      2 Export data as CSV",
        "      1 Fix the login crash",
    ]
    res = invoke(juniq, ["same", "--show-groups"], REQUESTS)
    assert res.lines[0] == "Please add dark mode to the app" and res.lines[1].startswith("  ↳ dark mode")
    res = invoke(juniq, ["same", "-d"], REQUESTS)
    assert res.lines == ["Please add dark mode to the app", "Export data as CSV"]
    res = invoke(juniq, ["same", "-u"], REQUESTS)
    assert res.lines == ["Fix the login crash"]
    res = invoke(juniq, ["same", "--json"], REQUESTS)
    rows = [json.loads(line) for line in res.lines]
    assert rows[0]["count"] == 3 and len(rows[0]["duplicates"]) == 2


def test_juniq_default_description_and_file_detection(invoke, tmp_path, mock):
    f = write(tmp_path, "r.txt", REQUESTS)
    res = invoke(juniq, [f])
    assert len(res.lines) == 3
    assert "the same thing said in different words" in mock.bodies[0]["questions"]["k0"]["instructions"]


def test_juniq_window_limits_comparisons(invoke, mock):
    lines = [f"unique line number {i} about topic {i}" for i in range(10)]
    invoke(juniq, ["same", "--window", "3"], "\n".join(lines) + "\n")
    assert max(len(b["questions"]) for b in mock.bodies) == 3


def test_juniq_fail_open_keeps_line_and_exit_5(invoke):
    res = invoke(juniq, ["same"], f"Export data as CSV\n{POISON} export data as CSV\n")
    assert res.code == 5 and len(res.lines) == 2


def test_juniq_usage_and_empty(invoke):
    assert invoke(juniq, ["x"], "").code == 1
    assert invoke(juniq, ["x", "--window", "0"]).code == 2
    assert invoke(juniq, ["x", "-d", "-u"]).code == 2
    assert invoke(juniq, ["x", "--show-groups", "-c"]).code == 2
    assert invoke(juniq, ["x", "--dry-run"], "a\n").code == 0


TICKETS = "The app crashes on launch\nPlease add export to CSV\nHow do I reset my password?\n"


def test_jtag_labels_add_a_column_and_keep_order(invoke, mock):
    res = invoke(jtag, ["--labels", "bug,feature,question"], TICKETS)
    assert res.lines == [
        "bug\tThe app crashes on launch",
        "feature\tPlease add export to CSV",
        "question\tHow do I reset my password?",
    ]
    assert mock.bodies[0]["questions"]["tag"]["type"] == "choice"


def test_jtag_suffix_sep_prob_json_default(invoke):
    res = invoke(jtag, ["--labels", "bug,feature,question", "--suffix", "--sep", "|", "--with-prob"], TICKETS)
    assert res.lines[0] == "The app crashes on launch|bug|0.80"
    res = invoke(jtag, ["--labels", "bug,feature,question", "--json"], TICKETS)
    obj = json.loads(res.lines[0])
    assert obj["label"] == "bug" and abs(sum(obj["probabilities"].values()) - 1) < 0.01
    res = invoke(jtag, ["--labels", "bug,feature,question", "--default", "other", "-p", "0.95"], TICKETS)
    assert all(line.startswith("other\t") for line in res.lines)


def test_jtag_score_modes(invoke, mock):
    text = "I love it, great, thanks\nmeh\n"
    res = invoke(jtag, ["--score", "how positive"], text)
    assert res.lines[0].split("\t")[0] == "0.95" and res.lines[1].split("\t")[0] == "0.05"
    res = invoke(jtag, ["--score", "how positive (0-100)"], text)
    assert res.lines[0].startswith("95\t") and res.lines[1].startswith("5\t")
    res = invoke(jtag, ["--score", "how positive", "--scale", "1-5"], text)
    assert res.lines[0].startswith("4.8\t")
    res = invoke(jtag, ["--score", "how positive", "--levels", "bad,ok,good"], text)
    assert mock.bodies[-1]["questions"]["tag"]["criteria"] == ["bad", "ok", "good"]
    res = invoke(jtag, ["--score", "how positive", "--json"], text)
    assert json.loads(res.lines[0])["score"] > 0.9


def test_jtag_blank_and_unjudged_lines_pass_through(invoke):
    res = invoke(jtag, ["--labels", "bug,feature"], f"crash\n\n{POISON}\n")
    assert res.lines == ["bug\tcrash", "", f"-\t{POISON}"] and res.code == 5


def test_jtag_usage(invoke):
    assert invoke(jtag, []).code == 2
    assert invoke(jtag, ["--labels", "one"]).code == 2
    assert invoke(jtag, ["--labels", "a,b", "--scale", "1-5"]).code == 2
    assert invoke(jtag, ["--score", "x", "--default", "y"]).code == 2
    assert invoke(jtag, ["--score", "x", "--scale", "nope"]).code == 2
    assert invoke(jtag, ["--labels", "a,b", "--default", "a"]).code == 2
    assert invoke(jtag, ["--labels", "a,b"], "").code == 1
    assert invoke(jtag, ["--labels", "a,b", "--dry-run"], "x\n").code == 0


INBOX = (
    "Can we get a quote for 50 enterprise seats?\n"
    "My password reset link is not working, please help\n"
    "FREE PRIZE winner click here\n"
    "\n"
    "Pricing for the team plan?\n"
)


def test_jroute_writes_bucket_files(invoke, tmp_path, mock):
    out = tmp_path / "sorted"
    res = invoke(jroute, ["sales:a sales lead", "support:a support request", "spam:junk", "--out-dir", str(out)], INBOX)
    assert res.code == 0
    assert (out / "sales.txt").read_text().splitlines() == [
        "Can we get a quote for 50 enterprise seats?",
        "Pricing for the team plan?",
    ]
    assert (out / "support.txt").read_text() == "My password reset link is not working, please help\n"
    assert (out / "spam.txt").read_text() == "FREE PRIZE winner click here\n"
    assert "4 lines routed" in res.err and "sales: 2" in res.err
    assert len(mock.bodies) == 4 and mock.bodies[0]["questions"]["bucket"]["type"] == "choice"


def test_jroute_stdout_json_default_and_truncate(invoke, tmp_path):
    out = tmp_path / "s"
    res = invoke(
        jroute, ["sales:lead", "support:help request", "spam:junk", "-o", str(out), "--stdout", "sales", "-q"], INBOX
    )
    assert res.lines == ["Can we get a quote for 50 enterprise seats?", "Pricing for the team plan?"] and res.err == ""
    res = invoke(jroute, ["sales:lead", "spam:junk", "--no-files", "--json"], INBOX)
    rows = [json.loads(line) for line in res.lines]
    assert rows[0]["bucket"] == "sales" and rows[2]["bucket"] == "spam"
    assert not (tmp_path / "sales.txt").exists()
    res = invoke(jroute, ["sales:lead", "spam:junk", "-o", str(out), "--default", "other", "-p", "0.95"], INBOX)
    assert (out / "other.txt").read_text().count("\n") == 4
    res = invoke(jroute, ["sales:lead", "spam:junk", "-o", str(out), "--truncate"], "Pricing?\n")
    assert (out / "sales.txt").read_text() == "Pricing?\n"


def test_jroute_input_files_and_unjudged_go_to_unrouted(invoke, tmp_path):
    src = write(tmp_path, "in.txt", f"Pricing?\n{POISON} line\n")
    out = tmp_path / "o"
    res = invoke(jroute, ["sales:lead", "spam:junk", "-i", src, "-o", str(out)])
    assert res.code == 5 and (out / "unrouted.txt").read_text() == f"{POISON} line\n"
    res = invoke(jroute, ["sales:lead", "spam:junk", "-i", src, "-o", str(out), "--default", "misc", "--truncate"])
    assert (out / "misc.txt").read_text() == f"{POISON} line\n"


def test_jroute_usage(invoke, tmp_path):
    assert invoke(jroute, ["only:one"]).code == 2
    assert invoke(jroute, ["a:x", "b:y", "--default", "a"]).code == 2
    assert invoke(jroute, ["a:x", "b:y", "--stdout", "zzz"]).code == 2
    assert invoke(jroute, ["a:x", "bad name:y"]).code == 2
    assert invoke(jroute, ["a:x", "b:y", "-o", str(tmp_path)], "").code == 1
    res = invoke(jroute, ["a:x", "b:y", "--dry-run"], "line\n")
    assert res.code == 0 and "a.txt" in res.out


def test_jroute_strict_stops(invoke, tmp_path):
    res = invoke(jroute, ["a:x", "b:y", "-o", str(tmp_path), "--strict"], f"{POISON}\n", mock_override=MockJev())
    assert res.code == 4


def test_jroute_truncate_clears_stale_buckets_it_does_not_write(invoke, tmp_path):
    """A rerun whose verdicts moved must not leave yesterday's lines in an untouched bucket."""
    out = tmp_path / "s"
    out.mkdir()
    (out / "unrouted.txt").write_text("a line from an earlier run\n")
    (out / "spam.txt").write_text("stale spam\n")
    res = invoke(
        jroute, ["sales:a sales lead", "spam:junk", "-o", str(out), "--truncate"], "Pricing for the team plan?\n"
    )
    assert res.code == 0
    assert (out / "sales.txt").read_text() == "Pricing for the team plan?\n"
    assert (out / "unrouted.txt").read_text() == ""  # nothing was unrouted this time
    assert (out / "spam.txt").read_text() == ""
    # Without --truncate the files are appended to, as before.
    res = invoke(jroute, ["sales:a sales lead", "spam:junk", "-o", str(out)], "Pricing again?\n")
    assert (out / "sales.txt").read_text().splitlines() == ["Pricing for the team plan?", "Pricing again?"]


def test_jtag_label_descriptions_may_contain_commas(invoke, mock):
    """A comma inside a description must not invent extra labels (it silently did once)."""
    spec = (
        "world:news about world affairs, politics or conflict,"
        "sports:news about sports,"
        "business:news about business, markets or the economy"
    )
    res = invoke(jtag, ["--labels", spec], "Arsenal won the cup final\n")
    assert res.code == 0
    criteria = mock.bodies[0]["questions"]["tag"]["criteria"]
    assert set(criteria) == {"world", "sports", "business"}
    assert criteria["world"] == "news about world affairs, politics or conflict"
    assert criteria["business"] == "news about business, markets or the economy"
    label, _, text = res.out.partition("\t")
    assert label in criteria and text.strip() == "Arsenal won the cup final"


def test_jtag_repeated_label_flag_is_unambiguous(invoke, mock):
    res = invoke(
        jtag,
        ["--label", "world:politics, war and diplomacy", "--label", "sports:games and athletes"],
        "Arsenal won the cup final\n",
    )
    assert res.code == 0
    assert set(mock.bodies[0]["questions"]["tag"]["criteria"]) == {"world", "sports"}
    assert invoke(jtag, ["--label", "onlyone:x"]).code == 2
    assert invoke(jtag, ["--label", "noseparator"]).code == 2
    assert invoke(jtag, ["--label", "a:x", "--labels", "b,c"]).code == 2  # mutually exclusive


def test_parse_labels_rejects_nonsense():
    from jevcore.errors import UsageError
    from jevcore.rubric import parse_labels

    assert parse_labels(r"a:x\,y,b:z") == {"a": "x,y", "b": "z"}
    with pytest.raises(UsageError):
        parse_labels("only one")
    with pytest.raises(UsageError):
        parse_labels("bad name:x,b:y")
    with pytest.raises(UsageError):
        parse_labels("a:x,a:y")


def test_jroute_bucket_names_stay_inside_the_out_dir(invoke, tmp_path):
    """A bucket name becomes a file name; it must not be able to name a path."""
    out = tmp_path / "s"
    assert invoke(jroute, ["a:x", "b:y", "-o", str(out), "--default", "../escape"], "line\n").code == 2
    assert invoke(jroute, ["a:x", "b:y", "-o", str(out), "--ext", "../../x"], "line\n").code == 2
    assert not (tmp_path.parent / "escape.txt").exists()


def test_jtag_levels_may_contain_an_escaped_comma(invoke, mock):
    res = invoke(jtag, ["--score", "how it went", "--levels", r"bad\, really bad,fine,great"], "it was fine\n")
    assert res.code == 0
    assert mock.bodies[0]["questions"]["tag"]["criteria"] == ["bad, really bad", "fine", "great"]
