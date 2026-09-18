"""jsort and jhead: ranking by the score primitive."""

import json

from jevcore.mock import POISON
from jevtools.jhead import main as jhead
from jevtools.jsort import main as jsort

FEEDBACK = (
    "Love the new dashboard, thanks team!\n"
    "This is the third time checkout has failed, I am done with this app!!\n"
    "How do I export my data to CSV?\n"
    "WHY does it log me out every five minutes?? furious, worst app\n"
)


def test_jsort_ranks_by_fit_desc_then_input_order(invoke, mock):
    res = invoke(jsort, ["angriest customer first"], FEEDBACK)
    assert res.code == 0
    assert res.lines[0].startswith("WHY does it log me out")  # three hits: "??", "furious", "worst"
    assert res.lines[1].startswith("This is the third time")  # two hits: "done with", "!!"
    assert res.lines[2:] == ["Love the new dashboard, thanks team!", "How do I export my data to CSV?"]
    assert all(b["questions"]["fit"]["type"] == "score" for b in mock.bodies)
    assert len(mock.bodies) == 4


def test_jsort_asc_limit_and_scores(invoke):
    res = invoke(jsort, ["angriest customer first", "--asc", "-n", "2", "--with-score"], FEEDBACK)
    assert len(res.lines) == 2 and res.lines[0].startswith("0.050\tLove the new dashboard")
    res = invoke(jsort, ["angriest", "--json", "-n", "1"], FEEDBACK)
    obj = json.loads(res.out)
    assert obj["rank"] == 1 and obj["score"] > 0.9 and obj["line"].startswith("WHY")
    assert obj["level"] == "fits the description perfectly"


def test_jsort_custom_levels(invoke, mock):
    invoke(jsort, ["urgent", "--levels", "calm,tense,panic"], "critical outage now\nfine\n")
    assert mock.bodies[0]["questions"]["fit"]["criteria"] == ["calm", "tense", "panic"]


def test_jsort_unjudged_go_last_and_exit_5(invoke):
    res = invoke(jsort, ["angriest"], f"furious!!\n{POISON} line\nmeh\n")
    assert res.code == 5 and res.lines[-1] == f"{POISON} line"


def test_jsort_empty_input_exit_1_and_blank_lines_dropped(invoke, mock):
    assert invoke(jsort, ["x"], "").code == 1
    res = invoke(jsort, ["x"], "\n\n")
    assert res.code == 1 and mock.bodies == []


def test_jsort_files_and_usage(invoke, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("furious!!\nfine\n")
    res = invoke(jsort, ["angry", str(f)])
    assert res.lines == ["furious!!", "fine"]
    assert invoke(jsort, []).code == 2
    assert invoke(jsort, ["x", "-n", "-1"]).code == 2
    assert invoke(jsort, ["x", "--levels", "one"]).code == 2


def test_jsort_dry_run(invoke, mock):
    res = invoke(jsort, ["angry", "--dry-run"], FEEDBACK)
    assert res.code == 0 and mock.bodies == [] and '"type": "score"' in res.out


def test_jhead_keeps_input_order(invoke):
    text = "meh\nfurious!!\nfine\nworst ever!!\nok\n"
    res = invoke(jhead, ["2", "angry customer"], text)
    assert res.lines == ["furious!!", "worst ever!!"]
    res = invoke(jhead, ["2", "angry customer", "--reorder", "--with-score"], text)
    assert len(res.lines) == 2 and all("\t" in line for line in res.lines)


def test_jhead_tail_zero_and_more_than_available(invoke, mock):
    text = "meh\nfurious!!\nfine\n"
    res = invoke(jhead, ["2", "angry", "--tail"], text)
    assert res.lines == ["meh", "fine"]
    mock.bodies.clear()
    res = invoke(jhead, ["0", "angry"], text)
    assert res.code == 0 and res.out == "" and mock.bodies == []
    res = invoke(jhead, ["10", "angry"], text)
    assert res.lines == ["meh", "furious!!", "fine"]


def test_jhead_json_and_unjudged(invoke):
    res = invoke(jhead, ["1", "angry", "--json"], "furious!!\nmeh\n")
    assert json.loads(res.out)["line"] == "furious!!"
    res = invoke(jhead, ["1", "angry"], f"{POISON}\nfurious!!\n")
    assert res.code == 5 and res.lines == ["furious!!"]


def test_jhead_usage(invoke):
    assert invoke(jhead, ["angry"]).code == 2
    assert invoke(jhead, ["x", "angry"]).code == 2
    assert invoke(jhead, ["-1", "angry"]).code == 2
    assert invoke(jhead, ["2", "angry", "--dry-run"], "a\n").code == 0
