"""jgrep against the mock: grep semantics, ordering, structured input, context, fail-open."""

import json

from jevcore.mock import POISON, MockJev
from jevtools.jgrep import main
from tests.conftest import write


def test_prints_matching_lines_in_input_order(invoke, mock):
    mock.delay = 0.01
    lines = [f"{i} {'alpha' if i % 3 == 0 else 'nothing'}" for i in range(40)]
    res = invoke(main, ["alpha", "-j", "8"], "\n".join(lines) + "\n")
    assert res.code == 0
    assert res.lines == [line for line in lines if "alpha" in line]
    assert len(mock.bodies) == 40 and mock.peak_in_flight <= 8


def test_no_match_exit_1_and_invert(invoke):
    res = invoke(main, ["alpha"], "plain one\nplain two\n")
    assert res.code == 1 and res.out == ""
    res = invoke(main, ["alpha", "-v"], "alpha one\nplain two\n")
    assert res.code == 0 and res.out == "plain two\n"


def test_prob_line_numbers_threshold(invoke):
    res = invoke(main, ["alpha", "-o", "-n", "-p", "0"], "alpha one\nplain two\n")
    assert res.out == "0.900\t1:alpha one\n0.100\t2:plain two\n"
    res = invoke(main, ["alpha", "-p", "0.95"], "alpha one\n")
    assert res.code == 1


def test_blank_lines_cost_nothing_and_never_match(invoke, mock):
    res = invoke(main, ["alpha"], "\n   \nalpha\n")
    assert res.out == "alpha\n" and len(mock.bodies) == 1
    res = invoke(main, ["alpha", "-v"], "\n   \nalpha\n")
    assert res.out == "\n   \n"


def test_multiple_descriptions_in_one_call(invoke, mock):
    text = "alpha here\nbeta here\nnothing\n"
    res = invoke(main, ["-e", "alpha", "-e", "beta"], text)
    assert res.lines == ["alpha here", "beta here"]
    assert all(len(b["questions"]) == 2 for b in mock.bodies)
    res = invoke(main, ["-e", "alpha", "-e", "beta", "--all"], "alpha and beta\nalpha only\n")
    assert res.lines == ["alpha and beta"]


def test_files_counts_and_filenames(invoke, tmp_path):
    a = write(tmp_path, "a.txt", "alpha\nalpha\nno\n")
    b = write(tmp_path, "b.txt", "no\n")
    assert invoke(main, ["alpha", a, "-c"]).out == "2\n"
    assert invoke(main, ["alpha", a, b, "-c"]).out == f"{a}:2\n{b}:0\n"
    assert invoke(main, ["alpha", a, b]).out == f"{a}:alpha\n{a}:alpha\n"
    assert invoke(main, ["alpha", a, b, "--no-filename"]).out == "alpha\nalpha\n"
    assert invoke(main, ["alpha", a, "-H"]).out == f"{a}:alpha\n{a}:alpha\n"


def test_files_with_matches_and_max_count(invoke, tmp_path):
    a = write(tmp_path, "a.txt", "alpha\n" * 30)
    b = write(tmp_path, "b.txt", "none\n")
    res = invoke(main, ["alpha", a, b, "-l"])
    assert res.out == f"{a}\n" and res.code == 0
    res = invoke(main, ["alpha", a, "-m", "3", "-j", "2"])
    assert res.out == "alpha\n" * 3 and len(res.mock.bodies) < 30
    res.mock.bodies.clear()
    res = invoke(main, ["alpha", a, "-m", "0"])
    assert res.out == "" and res.code == 1 and res.mock.bodies == []


def test_quiet_stops_at_first_match(invoke, mock):
    mock.delay = 0.005
    res = invoke(main, ["alpha", "-q", "-j", "2"], "alpha\n" * 200)
    assert res.code == 0 and res.out == "" and len(mock.bodies) < 200


def test_json_output(invoke):
    res = invoke(main, ["alpha", "--json"], "alpha one\n")
    obj = json.loads(res.out)
    assert obj == {"file": "(standard input)", "line": 1, "p": 0.9, "text": "alpha one"}


def test_paragraphs_and_whole(invoke, tmp_path):
    text = "alpha line\nsecond line\n\nno match\nhere\n"
    res = invoke(main, ["alpha", "--para"], text)
    assert res.out == "alpha line\nsecond line\n\n"
    assert len(res.mock.bodies) == 2  # two paragraphs, two calls
    res.mock.bodies.clear()
    a = write(tmp_path, "a.txt", text)
    b = write(tmp_path, "b.txt", "nothing\n")
    res = invoke(main, ["alpha", "--whole", a, b])
    assert res.out == f"{a}\n" and len(res.mock.bodies) == 2


def test_jsonl_and_csv_fields(invoke, tmp_path):
    j = write(tmp_path, "e.jsonl", '{"msg":"alpha","id":1}\n{"msg":"beta","id":2}\n')
    res = invoke(main, ["alpha", "--jsonl", "--field", "msg", j])
    assert res.out == '{"msg":"alpha","id":1}\n'
    res = invoke(main, ["alpha", "--jsonl", "--field", "msg", "--json", j])
    obj = json.loads(res.out)
    assert obj["record"] == {"msg": "alpha", "id": 1} and obj["field"] == "msg"
    c = write(tmp_path, "t.csv", 'id,text\n1,"alpha, yes"\n2,beta\n')
    res.mock.bodies.clear()
    res = invoke(main, ["alpha", "--csv", "--field", "text", c])
    assert res.out == 'id,text\n1,"alpha, yes"\n'
    # Only the named column is judged; the id and the raw row never reach the model. (A row whose
    # field was judged in an earlier leg of this test is served from the cache, so the set of
    # states actually sent is a subset, not an equality.)
    assert res.mock.bodies and {b["state"] for b in res.mock.bodies} <= {"alpha, yes", "beta"}


def test_context_sends_neighbours_but_prints_one_line(invoke, mock):
    res = invoke(main, ["alpha", "-C", "1"], "one\nalpha\nthree\nfour\n")
    assert res.out == "alpha\n"
    states = [b["state"] for b in mock.bodies]
    assert "  one\n> alpha\n  three" in states
    assert any(
        q["instructions"].startswith('The lines marked ">"') for b in mock.bodies for q in b["questions"].values()
    )


def test_recursive_search(invoke, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.txt").write_text("alpha\n")
    (tmp_path / "d" / "y.md").write_text("alpha\n")
    res = invoke(main, ["alpha", "-r", "--glob", "*.txt", "d"])
    assert res.out == "d/x.txt:alpha\n"
    res = invoke(main, ["alpha", "d"])
    assert res.code == 2 and "is a directory" in res.err


def test_missing_file_is_exit_2_but_others_still_searched(invoke, tmp_path):
    a = write(tmp_path, "a.txt", "alpha\n")
    res = invoke(main, ["alpha", str(tmp_path / "nope.txt"), a])
    assert res.code == 2 and "alpha" in res.out and "no such file" in res.err


def test_fail_open_passes_unjudged_lines_and_exits_5(invoke):
    res = invoke(main, ["alpha"], f"alpha\n{POISON} line\nplain\n")
    assert res.code == 5 and res.lines == ["alpha", f"{POISON} line"]
    assert "could not be judged" in res.err


def test_strict_stops_with_exit_4(invoke):
    res = invoke(main, ["alpha", "--strict"], f"alpha\n{POISON} line\nplain\n")
    assert res.code == 4 and "HTTP 400" in res.err


def test_dead_endpoint_reports_once_and_keeps_flowing(invoke):
    mock = MockJev(script=[500] * 40)
    res = invoke(
        main, ["alpha", "--timeout", "0.3", "-j", "4"], "".join(f"line {i}\n" for i in range(10)), mock_override=mock
    )
    assert res.code == 5 and len(res.lines) == 10
    assert res.err.count("gave up") == 1  # throttled to one report per minute


def test_auth_error_is_exit_3(invoke, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    res = invoke(main, ["alpha"], "x\n")
    assert res.code == 3 and "no API key" in res.err
    res = invoke(main, ["alpha"], "x\n", mock_override=MockJev(script=[401]))
    assert res.code == 3


def test_usage_errors_are_exit_2(invoke):
    assert invoke(main, []).code == 2
    assert invoke(main, ["x", "-p", "2"]).code == 2
    assert invoke(main, ["x", "--jsonl"]).code == 2
    assert invoke(main, ["x", "--whole", "-C", "2"]).code == 2
    assert invoke(main, ["x", "-m", "-1"]).code == 2
    assert invoke(main, ["x", "-j", "0"]).code == 2


def test_dry_run_sends_nothing(invoke, mock):
    res = invoke(main, ["a secret about bob@example.com", "--dry-run"], "card 4111111111111111\n")
    assert res.code == 0 and mock.bodies == []
    assert "dry run" in res.out and "4111111111111111" not in res.out and "typesafe" in res.out
    assert '"type": "noul"' in res.out


def test_budget_stops_the_run_with_exit_4(invoke):
    mock = MockJev(fixed_tokens=1_000_000)
    lines = "".join(f"alpha {i}\n" for i in range(10))  # distinct lines: the cache must not save us
    res = invoke(main, ["alpha", "--budget", "0.05", "-j", "1"], lines, mock_override=mock)
    assert res.code == 4 and "budget" in res.err and 1 <= len(res.lines) < 10


def test_stats_line_on_request(invoke):
    res = invoke(main, ["alpha", "--stats"], "alpha\n")
    assert "1 calls" in res.err and "$" in res.err


def test_unordered_prints_as_answers_arrive(invoke):
    res = invoke(main, ["alpha", "--unordered"], "alpha 1\nalpha 2\n")
    assert sorted(res.lines) == ["alpha 1", "alpha 2"]


def test_help_and_version(invoke):
    res = invoke(main, ["--help"])
    assert res.code == 0
    res = invoke(main, ["--version"])
    assert res.code == 0
