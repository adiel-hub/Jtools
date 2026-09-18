"""jgate (exit status only) and jwatch (streaming alerts)."""

import json
import os

from jevcore.mock import POISON, MockJev
from jevtools.jgate import main as jgate
from jevtools.jwatch import main as jwatch

LOGS = (
    "INFO  request served in 12ms\n"
    "ERROR payment service failed: connection refused\n"
    "INFO  cache warm\n"
    "ERROR unauthorized access attempt\n"
)


def test_jgate_whole_input_exit_codes_and_silence(invoke, mock):
    res = invoke(jgate, ["mentions an error"], LOGS)
    assert res.code == 0 and res.out == "" and len(mock.bodies) == 1
    assert mock.bodies[0]["state"] == LOGS.rstrip("\n")
    res = invoke(jgate, ["mentions a giraffe"], LOGS)
    assert res.code == 1 and res.out == ""


def test_jgate_print_passthrough_and_json(invoke):
    res = invoke(jgate, ["mentions an error", "-P"], LOGS)
    assert res.code == 0 and res.out == LOGS
    res = invoke(jgate, ["mentions a giraffe", "-P"], LOGS)
    assert res.code == 1 and res.out == ""
    res = invoke(jgate, ["mentions an error", "--json"], LOGS)
    assert json.loads(res.out) == {"pass": True, "probability": 0.9, "threshold": 0.5}


def test_jgate_fails_closed_by_default_and_open_on_request(invoke):
    res = invoke(jgate, ["x"], f"{POISON}\n")
    assert res.code == 4 and "failing closed" in res.err
    res = invoke(jgate, ["x", "--fail-open"], f"{POISON}\n")
    assert res.code == 0
    dead = MockJev(script=[500] * 10)
    res = invoke(jgate, ["x", "--timeout", "0.3"], "text\n", mock_override=dead)
    assert res.code == 4
    assert invoke(jgate, ["x", "--fail-open", "--strict"]).code == 2


def test_jgate_each_any_and_all(invoke, mock):
    mock.delay = 0.005
    res = invoke(jgate, ["an error", "--each", "-j", "2"], LOGS)
    assert res.code == 0 and len(mock.bodies) >= 1
    res = invoke(jgate, ["an error", "--all"], LOGS)
    assert res.code == 1
    res = invoke(jgate, ["an error", "--all"], "error one\nerror two\n")
    assert res.code == 0
    res = invoke(jgate, ["an error", "--each", "-P"], LOGS)
    assert res.code == 0 and res.out == LOGS
    res = invoke(jgate, ["an error", "--each", "--json"], LOGS)
    obj = json.loads(res.out)
    assert obj["pass"] is True and obj["mode"] == "any"


def test_jgate_all_with_unjudged_line_fails_closed(invoke):
    res = invoke(jgate, ["an error", "--all"], f"error one\n{POISON} error\n")
    assert res.code == 4
    res = invoke(jgate, ["an error", "--all", "--fail-open"], f"error one\n{POISON} error\n")
    assert res.code == 0


def test_jgate_empty_input_and_usage(invoke):
    assert invoke(jgate, ["x"], "").code == 1
    assert invoke(jgate, ["x"], "\n\n").code == 1
    assert invoke(jgate, []).code == 2
    assert invoke(jgate, ["x", "--each", "--all"]).code == 2
    res = invoke(jgate, ["x", "--dry-run"], "hello\n")
    assert res.code == 0 and "ONE state" in res.out


def test_jgate_verbose_prints_probability(invoke):
    res = invoke(jgate, ["mentions an error", "-v"], LOGS)
    assert "p=0.900" in res.err


def test_jwatch_prints_only_alerts_in_order(invoke, mock):
    mock.delay = 0.01
    res = invoke(jwatch, ["an error", "-j", "4"], LOGS)
    assert res.code == 0
    assert res.lines == ["ERROR payment service failed: connection refused", "ERROR unauthorized access attempt"]
    res = invoke(jwatch, ["a giraffe"], LOGS)
    assert res.code == 1 and res.out == ""


def test_jwatch_score_json_and_all_lines(invoke):
    res = invoke(jwatch, ["an error", "--with-score"], LOGS)
    assert res.lines[0].startswith("0.900\tERROR")
    res = invoke(jwatch, ["an error", "--json"], LOGS)
    rows = [json.loads(line) for line in res.lines]
    assert rows[0]["alert"] is True and rows[0]["probability"] == 0.9
    res = invoke(jwatch, ["an error", "--all-lines"], LOGS)
    assert len(res.lines) == 4 and res.lines[0].startswith("   INFO")


def test_jwatch_cooldown_collapses_bursts(invoke):
    res = invoke(jwatch, ["an error", "--cooldown", "60"], "error 1\nerror 2\nerror 3\nfine\n")
    assert res.lines == ["error 1"] and "2 alert(s) were suppressed" in res.err
    res = invoke(jwatch, ["an error", "--cooldown", "60", "-v"], "error 1\nerror 2\n")
    assert "suppressed by cooldown" in res.err


def test_jwatch_max_alerts_stops_reading(invoke, mock):
    mock.delay = 0.002
    res = invoke(jwatch, ["an error", "--max", "2", "-j", "2"], "error\n" * 100)
    assert res.lines == ["error", "error"] and len(mock.bodies) < 100


def test_jwatch_exec_runs_command_with_quoted_line(invoke, tmp_path):
    target = tmp_path / "hits.txt"
    res = invoke(jwatch, ["an error", "--exec", f"echo {{}} >> {target}"], "error it's here\nfine\n")
    assert res.code == 0
    assert target.read_text() == "error it's here\n"
    res = invoke(jwatch, ["an error", "--exec", f"echo >> {target}"], "error two\n")  # {} appended
    assert "error two" in target.read_text()
    assert os.path.exists(target)


def test_jwatch_fail_open_and_usage(invoke):
    res = invoke(jwatch, ["an error"], f"{POISON} error\nerror\n")
    assert res.code == 5 and res.lines == ["error"]
    assert invoke(jwatch, []).code == 2
    assert invoke(jwatch, ["x", "--cooldown", "-1"]).code == 2
    assert invoke(jwatch, ["x", "--max", "0"]).code == 2
    assert invoke(jwatch, ["x", "--dry-run"], "a\n").code == 0
