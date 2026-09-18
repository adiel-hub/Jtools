"""jpick and jmatch: choice over candidate sets, with tournaments."""

import json

from jevcore.mock import POISON, MockJev, Rule
from jevtools.jmatch import main as jmatch
from jevtools.jpick import chunks
from jevtools.jpick import main as jpick
from tests.conftest import write

SUBJECTS = "Your invoice\nURGENT: account suspended act now\nWeekly digest\nHello\n"


def test_jpick_returns_exactly_one(invoke, mock):
    res = invoke(jpick, ["most urgent"], SUBJECTS)
    assert res.code == 0 and res.lines == ["URGENT: account suspended act now"]
    body = mock.bodies[0]
    assert isinstance(body["state"], list) and body["state"][0] == {"id": "c1", "text": "Your invoice"}
    assert body["questions"]["best"]["type"] == "choice" and set(body["questions"]["best"]["criteria"]) == {
        "c1",
        "c2",
        "c3",
        "c4",
    }


def test_jpick_why_top_and_json(invoke):
    res = invoke(jpick, ["most urgent", "--why"], SUBJECTS)
    assert res.lines[0].startswith("URGENT: account suspended act now\t# p=0.80 vs 0.07 for")
    res = invoke(jpick, ["most urgent", "--top", "2", "--with-score"], SUBJECTS)
    assert len(res.lines) == 2 and res.lines[0].startswith("0.800\tURGENT")
    res = invoke(jpick, ["most urgent", "--json"], SUBJECTS)
    obj = json.loads(res.out)
    assert obj["rank"] == 1 and obj["probability"] == 0.8 and obj["runner_up"]


def test_jpick_tournament_over_many_lines(invoke, mock):
    lines = [f"subject {i}" for i in range(100)]
    lines[57] = "URGENT outage now"
    res = invoke(jpick, ["most urgent", "--group", "10"], "\n".join(lines) + "\n")
    assert res.lines == ["URGENT outage now"]
    # 100 -> 10 groups -> 10 winners -> 1 final: 11 calls, each with at most 10 candidates
    assert len(mock.bodies) == 11 and all(len(b["state"]) <= 10 for b in mock.bodies)


def test_jpick_top_n_survives_rounds(invoke):
    lines = [f"subject {i}" for i in range(30)]
    lines[3] = "URGENT one now"
    lines[20] = "critical outage asap"
    res = invoke(jpick, ["most urgent", "--group", "10", "--top", "2"], "\n".join(lines) + "\n")
    assert set(res.lines) == {"URGENT one now", "critical outage asap"}


def test_jpick_single_line_needs_no_call(invoke, mock):
    res = invoke(jpick, ["anything"], "only one\n")
    assert res.lines == ["only one"] and mock.bodies == []


def test_jpick_failed_calls_fall_back_to_input_order(invoke):
    mock = MockJev(script=[500] * 20)
    res = invoke(jpick, ["most urgent", "--timeout", "0.3"], SUBJECTS, mock_override=mock)
    assert res.code == 5 and res.lines == ["Your invoice"]


def test_jpick_transient_failure_keeps_the_group_and_recovers(invoke):
    lines = [f"subject {i}" for i in range(20)]
    lines[15] = "URGENT now"
    mock = MockJev(script=[500, 500, 500, 500])  # the first group's call fails after 4 attempts; the rest work
    res = invoke(jpick, ["most urgent", "--group", "10", "--timeout", "5"], "\n".join(lines) + "\n", mock_override=mock)
    # the failed group is kept whole and narrowed in the next round; the right line still wins
    assert res.lines == ["URGENT now"] and res.code == 5


def test_jpick_poisoned_line_degrades_to_input_order_not_a_crash(invoke):
    lines = [f"subject {i}" for i in range(20)]
    lines[2] = f"{POISON} subject"
    lines[15] = "URGENT now"
    res = invoke(jpick, ["most urgent", "--group", "10"], "\n".join(lines) + "\n")
    # every group containing the poisoned line fails; jpick falls back to input order and says so
    assert res.code == 5 and len(res.lines) == 1 and res.lines[0] in lines and "failed" in res.err


def test_jpick_usage_and_dry_run(invoke, mock):
    assert invoke(jpick, []).code == 2
    assert invoke(jpick, ["x", "--top", "0"]).code == 2
    assert invoke(jpick, ["x", "--group", "1"]).code == 2
    assert invoke(jpick, ["x", "--top", "5", "--group", "3"]).code == 2
    assert invoke(jpick, ["x"], "").code == 1
    res = invoke(jpick, ["x", "--dry-run"], "a\nb\n")
    assert res.code == 0 and mock.bodies == [] and "JSON array" in res.out


def test_chunks_are_balanced():
    from jevcore.inputs import Record

    items = [Record(i, str(i), "x", i) for i in range(13)]
    sizes = [len(c) for c in chunks(items, 12)]
    assert sizes == [7, 6]
    assert [len(c) for c in chunks(items[:12], 12)] == [12]
    assert [len(c) for c in chunks(items, 5)] == [5, 4, 4]


def test_jmatch_pairs_by_word_overlap(invoke, tmp_path, mock):
    a = write(tmp_path, "a.txt", "Acme Corp invoice 42\nGlobex payment\nUnrelated thing\n")
    b = write(tmp_path, "b.txt", "payment from Globex\nAcme invoice paid\n")
    res = invoke(jmatch, [a, b, "same transaction"])
    assert res.code == 0
    assert res.lines == ["Acme Corp invoice 42\tAcme invoice paid\t0.800", "Globex payment\tpayment from Globex\t0.800"]
    body = mock.bodies[0]
    assert body["state"]["target"] and "none" in body["questions"]["match"]["criteria"]


def test_jmatch_unmatched_format_and_json(invoke, tmp_path):
    a = write(tmp_path, "a.txt", "Acme invoice\nzzz\n")
    b = write(tmp_path, "b.txt", "Acme invoice paid\n")
    res = invoke(jmatch, [a, b, "same", "--unmatched", "--format", "{a} => {b} ({score})"])
    assert res.lines == ["Acme invoice => Acme invoice paid (0.800)", "zzz =>  (0.800)"]
    res = invoke(jmatch, [a, b, "same", "--json"])
    rows = [json.loads(line) for line in res.lines]
    assert rows[0]["matched"] is True and rows[1]["matched"] is False and rows[1]["b"] is None


def test_jmatch_threshold_tournament_and_shortlist(invoke, tmp_path, mock):
    a = write(tmp_path, "a.txt", "Acme invoice\n")
    b_lines = [f"candidate {i}" for i in range(25)] + ["Acme invoice paid"]
    b = write(tmp_path, "b.txt", "\n".join(b_lines) + "\n")
    res = invoke(jmatch, [a, b, "same", "--group", "10"])
    assert res.lines == ["Acme invoice\tAcme invoice paid\t0.800"]
    assert len(mock.bodies) == 4  # 3 groups + final
    mock.bodies.clear()
    res = invoke(jmatch, [a, b, "same", "--shortlist", "5"])
    assert res.lines == ["Acme invoice\tAcme invoice paid\t0.800"] and len(mock.bodies) == 1
    res = invoke(jmatch, [a, b, "same", "-p", "0.9"])
    assert res.code == 1 and res.out == ""


def test_jmatch_stdin_and_usage(invoke, tmp_path):
    b = write(tmp_path, "b.txt", "Acme invoice paid\n")
    res = invoke(jmatch, ["-", b, "same"], "Acme invoice\n")
    assert res.lines == ["Acme invoice\tAcme invoice paid\t0.800"]
    assert invoke(jmatch, ["-", "-", "same"]).code == 2
    assert invoke(jmatch, ["a", "b"]).code == 2
    assert invoke(jmatch, ["a", b, "x", "--format", "{nope}"]).code == 2
    empty = write(tmp_path, "e.txt", "")
    assert invoke(jmatch, [empty, b, "x"]).code == 1
    assert invoke(jmatch, [b, empty, "x"]).code == 1


def test_jmatch_fail_open(invoke, tmp_path):
    a = write(tmp_path, "a.txt", f"{POISON} target\nAcme invoice\n")
    b = write(tmp_path, "b.txt", "Acme invoice paid\n")
    res = invoke(jmatch, [a, b, "same", "--unmatched"])
    assert res.code == 5 and res.lines[0] == f"{POISON} target\t\t-" and res.lines[1].startswith("Acme invoice\t")


def test_jmatch_rule_override_for_none(invoke, tmp_path):
    a = write(tmp_path, "a.txt", "Acme invoice\n")
    b = write(tmp_path, "b.txt", "Acme invoice paid\n")
    mock = MockJev(rules=[Rule({"type": "choice", "choice": "none", "probabilities": {"c1": 0.1, "none": 0.9}})])
    res = invoke(jmatch, [a, b, "same"], mock_override=mock)
    assert res.code == 1 and res.out == ""
