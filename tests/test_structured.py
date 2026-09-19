"""``--jsonl`` / ``--csv`` / ``--field`` across the tools, and the output they have to produce.

Only jgrep could read a structured file. Handed a CSV, every other tool judged the header row as
if it were data -- a real call, a nonsense verdict -- and wrote back a pile of rows with nothing
saying what the columns were. These tests pin down both halves: what is judged, and what comes out.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from jevtools.jgate import main as jgate
from jevtools.jgrep import main as jgrep
from jevtools.jhead import main as jhead
from jevtools.jmatch import main as jmatch
from jevtools.jpick import main as jpick
from jevtools.jroute import main as jroute
from jevtools.jsort import main as jsort
from jevtools.jtag import main as jtag
from jevtools.juniq import main as juniq
from jevtools.jwatch import main as jwatch
from tests.conftest import write

ROWS = 'id,note\n1,alpha one\n2,plain two\n3,"alpha, with a comma"\n'
OBJECTS = '{"id": 1, "note": "alpha one"}\n{"id": 2, "note": "plain two"}\n'

TICKETS = "id,note\n1,the checkout is broken\n2,please add dark mode\n3,how do I export my data\n"
TICKET_OBJECTS = (
    '{"id": 1, "note": "the checkout is broken"}\n'
    '{"id": 2, "note": "please add dark mode"}\n'
    '{"id": 3, "note": "how do I export my data"}\n'
)

HEADER = ["id", "note"]


# --------------------------------------------------------------------- jtag


def test_jtag_does_not_judge_the_csv_header(invoke, tmp_path):
    """The header is the shape of the data, not a row of it.

    Fed a CSV as plain lines, jtag spent a real call labelling `id,note` and put the verdict in
    the output as though it were a ticket.
    """
    f = write(tmp_path, "rows.csv", ROWS)
    res = invoke(jtag, ["--csv", "--field", "note", "--labels", "alpha,plain", f])
    assert len(res.mock.bodies) == 3, f"judged {len(res.mock.bodies)} records for 3 data rows"
    assert all("id,note" not in json.dumps(b["state"]) for b in res.mock.bodies)


def test_jtag_writes_a_csv_a_spreadsheet_can_open(invoke, tmp_path):
    f = write(tmp_path, "rows.csv", ROWS)
    res = invoke(jtag, ["--csv", "--field", "note", "--labels", "alpha,plain", f])
    rows = list(csv.reader(io.StringIO(res.out)))
    assert rows[0] == ["label", "id", "note"]
    assert rows[1] == ["alpha", "1", "alpha one"]
    # the cell that contains the separator survives, which a tab-joined label could not promise
    assert rows[3] == ["alpha", "3", "alpha, with a comma"]


def test_suffix_puts_the_column_last_in_the_header_too(invoke, tmp_path):
    f = write(tmp_path, "rows.csv", ROWS)
    res = invoke(jtag, ["--csv", "--field", "note", "--suffix", "--labels", "alpha,plain", f])
    rows = list(csv.reader(io.StringIO(res.out)))
    assert rows[0] == ["id", "note", "label"]
    assert rows[1] == ["1", "alpha one", "alpha"]


def test_jtag_adds_a_key_to_a_json_record(invoke, tmp_path):
    f = write(tmp_path, "rows.jsonl", OBJECTS)
    res = invoke(jtag, ["--jsonl", "--field", "note", "--labels", "alpha,plain", f])
    objects = [json.loads(line) for line in res.lines]
    assert objects[0] == {"label": "alpha", "id": 1, "note": "alpha one"}
    assert list(objects[0]) == ["label", "id", "note"], "the new key should come first without --suffix"


def test_the_new_column_can_be_named(invoke, tmp_path):
    f = write(tmp_path, "rows.csv", ROWS)
    res = invoke(jtag, ["--csv", "--field", "note", "--column", "triage", "--labels", "alpha,plain", f])
    assert next(csv.reader(io.StringIO(res.out))) == ["triage", "id", "note"]


def test_with_prob_adds_a_second_column_not_a_second_value(invoke, tmp_path):
    """`bug\tab0.85` in one cell is two things glued into one value, which is the whole failure
    this mode exists to avoid."""
    f = write(tmp_path, "rows.csv", ROWS)
    res = invoke(jtag, ["--csv", "--field", "note", "--with-prob", "--labels", "alpha,plain", f])
    rows = list(csv.reader(io.StringIO(res.out)))
    assert rows[0] == ["label", "label_probability", "id", "note"]
    assert rows[1][0] == "alpha"
    assert 0.0 <= float(rows[1][1]) <= 1.0
    assert rows[1][2:] == ["1", "alpha one"]


def test_a_number_written_into_a_json_record_is_a_number(invoke, tmp_path):
    """A JSON record is read by a program: `jq 'select(.score > 0.9)'` cannot compare a string."""
    f = write(tmp_path, "rows.jsonl", OBJECTS)
    res = invoke(jtag, ["--jsonl", "--field", "note", "--score", "how alpha it is", "--with-prob", f])
    first = json.loads(res.lines[0])
    assert isinstance(first["score"], int | float) and not isinstance(first["score"], bool)
    assert isinstance(first["score_confidence"], int | float)


def test_a_label_that_reads_like_a_number_stays_a_string(invoke, tmp_path):
    f = write(tmp_path, "rows.jsonl", OBJECTS)
    res = invoke(jtag, ["--jsonl", "--field", "note", "--labels", "1:alpha,2:plain", f])
    assert json.loads(res.lines[0])["label"] in {"1", "2"}


def test_a_name_already_in_the_record_is_reported(invoke, tmp_path):
    """Running jtag over its own output is the ordinary way to reach this."""
    f = write(tmp_path, "rows.csv", ROWS)
    res = invoke(jtag, ["--csv", "--field", "note", "--column", "note", "--labels", "alpha,plain", f])
    assert "already in the record" in res.err
    assert res.err.count("already in the record") == 1, "said once, not once per row"


def test_no_escape_sequence_reaches_a_cell(invoke, tmp_path):
    f = write(tmp_path, "rows.csv", ROWS)
    res = invoke(jtag, ["--csv", "--color", "always", "--field", "note", "--labels", "alpha,plain", f])
    assert "\x1b" not in res.out, "a colour code inside a CSV cell is not the value"


def test_a_json_line_that_is_not_an_object_still_gets_its_verdict(invoke, tmp_path):
    """An array or a bare string has no key to add, so the label goes beside it, not inside it."""
    f = write(tmp_path, "rows.jsonl", '["alpha one"]\n"plain two"\n')
    res = invoke(jtag, ["--jsonl", "--labels", "alpha,plain", f])
    assert res.code == 0, res.err
    assert res.lines[0].startswith("alpha\t")


# ------------------------------------------- the header is never judged, in any tool

JUDGES_EVERY_ROW = [
    pytest.param(jsort, ["a bug"], id="jsort"),
    pytest.param(jhead, ["2", "a bug"], id="jhead"),
    pytest.param(juniq, ["the same problem"], id="juniq"),
    pytest.param(jtag, ["--labels", "bug,feature,question"], id="jtag"),
    pytest.param(jgate, ["--each", "a bug"], id="jgate"),
    pytest.param(jwatch, ["a bug"], id="jwatch"),
    pytest.param(jgrep, ["a bug"], id="jgrep"),
]


@pytest.mark.parametrize(("tool", "argv"), JUDGES_EVERY_ROW)
def test_the_csv_header_is_never_a_record(invoke, tmp_path, tool, argv):
    """Three data rows, three decisions: the header costs nothing and cannot be judged.

    Column *names* are of course inside every record; what must never appear is a record whose
    values are those names, which is what the header row becomes when it is read as data.
    """
    f = write(tmp_path, "t.csv", TICKETS)
    res = invoke(tool, ["--csv", *argv, f])
    states = [b["state"] for b in res.mock.bodies]
    assert "the checkout is broken" in json.dumps(states), res.err
    # At most one decision per data row -- juniq keeps the first line for free and jgate --each
    # stops at the first hit, so the exact count is the tool's business; the header is not.
    assert 0 < len(states) <= 3, f"{len(states)} decisions for 3 data rows"
    assert "id,note" not in json.dumps(states), f"the header reached Jev as text: {states}"
    header_record = {"id": "id", "note": "note"}
    # juniq nests the records it compares, so look inside a state as well as at it.
    nested = [v for s in states if isinstance(s, dict) for v in s.values()]
    flat = [v for item in nested for v in (item if isinstance(item, list) else [item])]
    assert header_record not in [*states, *flat], f"the header was judged as a row: {states}"


def test_reading_a_csv_as_lines_is_what_costs_the_extra_call(invoke, tmp_path):
    """The control for the test above: without --csv the header is a line like any other."""
    f = write(tmp_path, "t.csv", TICKETS)
    plain = invoke(jsort, ["a bug", f])
    assert len(plain.mock.bodies) == 4, "the header is a fourth line, and a fourth call"
    assert plain.lines[0] != "id,note", "sorted among the data, which is what --csv is for"
    before = len(plain.mock.bodies)  # one mock serves the whole test, so the bodies accumulate
    structured = invoke(jsort, ["--csv", "a bug", f])
    assert len(structured.mock.bodies) - before == 3


@pytest.mark.parametrize(("tool", "argv"), JUDGES_EVERY_ROW)
def test_a_whole_record_goes_as_an_object(invoke, tmp_path, tool, argv):
    """Jev reads JSON natively, so a record with no --field is sent as the object it is.

    Sent as text it would arrive as a string full of braces and quotes, which is a description of
    a record rather than a record.
    """
    f = write(tmp_path, "t.jsonl", TICKET_OBJECTS)
    res = invoke(tool, ["--jsonl", *argv, f])
    states = [b["state"] for b in res.mock.bodies]
    assert states, res.err
    found = [s for s in states if isinstance(s, dict) and "note" in s]
    nested = [v for s in states if isinstance(s, dict) for v in s.values() if isinstance(v, dict | list)]
    assert found or nested, f"no record reached Jev as an object: {states}"


# --------------------------------------------------- the header comes back, once and first


@pytest.mark.parametrize(
    ("tool", "argv"),
    [
        pytest.param(jsort, ["a bug"], id="jsort"),
        pytest.param(jhead, ["2", "a bug"], id="jhead"),
        pytest.param(jpick, ["a bug"], id="jpick"),
        pytest.param(juniq, ["the same problem"], id="juniq"),
        pytest.param(jgrep, ["-p", "0", "a bug"], id="jgrep"),
    ],
)
def test_the_output_is_still_a_csv(invoke, tmp_path, tool, argv):
    f = write(tmp_path, "t.csv", TICKETS)
    res = invoke(tool, ["--csv", *argv, f])
    rows = list(csv.reader(io.StringIO(res.out)))
    assert rows[0] == HEADER, f"first line is not the header: {res.out!r}"
    assert [r for r in rows[1:] if r == HEADER] == [], "the header was written more than once"
    assert all(len(r) == len(HEADER) for r in rows), f"a row has the wrong number of columns: {rows}"


@pytest.mark.parametrize(
    ("tool", "argv"),
    [
        pytest.param(jsort, ["-s", "a bug"], id="jsort -s"),
        pytest.param(jhead, ["-s", "2", "a bug"], id="jhead -s"),
        pytest.param(jpick, ["-s", "a bug"], id="jpick -s"),
        pytest.param(juniq, ["-c", "the same problem"], id="juniq -c"),
        pytest.param(juniq, ["--show-groups", "the same problem"], id="juniq --show-groups"),
        pytest.param(jgrep, ["-p", "0", "-o", "a bug"], id="jgrep -o"),
        pytest.param(jgrep, ["-p", "0", "-n", "a bug"], id="jgrep -n"),
    ],
)
def test_a_prefixed_row_gets_no_header(invoke, tmp_path, tool, argv):
    """A score or a line number in front of the row makes the output a view, not a file.

    The columns no longer line up with the header, so printing one would be a claim the output
    cannot keep. `jtag` is the tool that puts a verdict *inside* the record.
    """
    f = write(tmp_path, "t.csv", TICKETS)
    res = invoke(tool, ["--csv", *argv, f])
    assert res.out, res.err
    assert "id,note" not in res.out, f"a header above prefixed rows: {res.out!r}"


def test_a_second_file_brings_its_own_header(invoke, tmp_path):
    """jgrep writes a header per input file, since each names its own columns."""
    a = write(tmp_path, "a.csv", TICKETS)
    b = write(tmp_path, "b.csv", "ref,detail\nX-1,the checkout is broken\n")
    res = invoke(jgrep, ["--csv", "--no-filename", "a bug", a, b])
    assert res.lines.count("id,note") == 1
    assert res.lines.count("ref,detail") == 1


def test_two_inputs_with_different_headers_are_reported(invoke, tmp_path):
    """These tools interleave every input's rows, so no single header describes the output."""
    a = write(tmp_path, "a.csv", TICKETS)
    b = write(tmp_path, "b.csv", "ref,detail\nX-1,the checkout is broken\n")
    res = invoke(jsort, ["--csv", "a bug", a, b])
    assert res.lines[0] == "id,note"
    assert "do not share a header" in res.err


def test_matching_headers_are_not_reported(invoke, tmp_path):
    a = write(tmp_path, "a.csv", TICKETS)
    b = write(tmp_path, "b.csv", "id,note\n9,another broken thing\n")
    res = invoke(jsort, ["--csv", "a bug", a, b])
    assert res.lines.count("id,note") == 1
    assert "do not share a header" not in res.err


def test_jroute_reports_inputs_that_disagree(invoke, tmp_path):
    a = write(tmp_path, "a.csv", TICKETS)
    b = write(tmp_path, "b.csv", "ref,detail\nX-1,the checkout is broken\n")
    res = invoke(jroute, ["--csv", "bug:broken", "ask:a request", "-i", a, "-i", b, "--no-files"])
    assert "do not share a header" in res.err


def test_a_csv_read_from_stdin_works_the_same(invoke):
    res = invoke(jsort, ["--csv", "a bug"], stdin=TICKETS)
    assert res.lines[0] == "id,note"
    assert len(res.lines) == 4


def test_json_output_has_no_csv_header(invoke, tmp_path):
    """--json is not a CSV, so a bare header line in the middle of it would be unparseable."""
    f = write(tmp_path, "t.csv", TICKETS)
    res = invoke(jsort, ["--csv", "--json", "a bug", f])
    for line in res.lines:
        json.loads(line)


def test_the_header_survives_a_run_that_matches_nothing(invoke, tmp_path):
    """jsort keeps every row, so the header is there whatever the verdicts were."""
    f = write(tmp_path, "t.csv", TICKETS)
    res = invoke(jsort, ["--csv", "nothing whatsoever like this", f])
    assert res.lines[0] == "id,note"
    assert len(res.lines) == 4


# --------------------------------------------------------------------- jmatch


CRM = "account_id,account_name\nA-10,the checkout\nA-11,dark mode\n"


def test_jmatch_reads_both_sides_as_records(invoke, tmp_path):
    a = write(tmp_path, "a.csv", TICKETS)
    b = write(tmp_path, "b.csv", CRM)
    res = invoke(jmatch, ["--csv", a, b, "the same subject"])
    assert res.code in (0, 1), res.err
    states = [body["state"] for body in res.mock.bodies]
    assert len(states) == 3, f"{len(states)} targets for 3 rows in FILE_A"
    assert {"id": "id", "note": "note"} not in [s["target"] for s in states], "A's header was a target"
    candidates = [c["record"] for s in states for c in s["candidates"]]
    assert {"account_id": "account_id", "account_name": "account_name"} not in candidates, "B's header was a candidate"
    assert len(states[0]["candidates"]) == 2, "FILE_B has two rows, not three"


def test_field_b_names_the_column_on_the_other_side(invoke, tmp_path):
    """A join is between two exports, and two exports rarely name a column the same way."""
    a = write(tmp_path, "a.csv", TICKETS)
    b = write(tmp_path, "b.csv", CRM)
    res = invoke(jmatch, ["--csv", "--field", "note", "--field-b", "account_name", a, b, "the same subject"])
    assert res.code in (0, 1), res.err
    state = res.mock.bodies[0]["state"]
    assert state["target"] == "the checkout is broken"
    assert [c["text"] for c in state["candidates"]] == ["the checkout", "dark mode"]


def test_field_b_alone_is_a_usage_error(invoke, tmp_path):
    a = write(tmp_path, "a.csv", TICKETS)
    b = write(tmp_path, "b.csv", CRM)
    assert invoke(jmatch, ["--field-b", "account_name", a, b, "x"]).code == 2


# --------------------------------------------------------------------- jroute


def test_jroute_buckets_are_csv_files(invoke, tmp_path):
    f = write(tmp_path, "t.csv", TICKETS)
    out = tmp_path / "sorted"
    res = invoke(jroute, ["--csv", "bug:something is broken", "feature:a request", "-i", f, "-o", str(out)])
    assert res.code == 0, res.err
    written = sorted(p.name for p in out.iterdir())
    assert written and all(name.endswith(".csv") for name in written), written
    for path in out.iterdir():
        rows = list(csv.reader(path.open()))
        assert rows[0] == HEADER, f"{path.name} has no header: {rows}"
        assert all(len(r) == len(HEADER) for r in rows), rows


def test_a_second_run_appends_without_repeating_the_header(invoke, tmp_path):
    f = write(tmp_path, "t.csv", TICKETS)
    out = tmp_path / "sorted"
    argv = ["--csv", "bug:something is broken", "feature:a request", "-i", f, "-o", str(out)]
    invoke(jroute, argv)
    invoke(jroute, argv)
    for path in out.iterdir():
        assert path.read_text().count("id,note") == 1, path.read_text()


def test_truncate_starts_the_file_with_its_header_again(invoke, tmp_path):
    f = write(tmp_path, "t.csv", TICKETS)
    out = tmp_path / "sorted"
    argv = ["--csv", "bug:something is broken", "feature:a request", "-i", f, "-o", str(out)]
    invoke(jroute, argv)
    invoke(jroute, [*argv, "--truncate"])
    for path in out.iterdir():
        assert path.read_text().startswith("id,note\n"), path.read_text()
        assert path.read_text().count("id,note") == 1


def test_the_bucket_sent_to_stdout_carries_the_header(invoke, tmp_path):
    f = write(tmp_path, "t.csv", TICKETS)
    res = invoke(
        jroute,
        ["--csv", "bug:something is broken", "feature:a request", "-i", f, "--no-files", "--stdout", "bug"],
    )
    assert res.lines[0] == "id,note", res.out
    assert len(list(csv.reader(io.StringIO(res.out)))) >= 2


def test_plain_input_still_writes_txt_buckets(invoke, tmp_path):
    """The extension follows the input, so nothing changes for the lines jroute was built for."""
    f = write(tmp_path, "t.txt", "the checkout is broken\nplease add dark mode\n")
    out = tmp_path / "sorted"
    invoke(jroute, ["bug:something is broken", "feature:a request", "-i", f, "-o", str(out)])
    assert all(p.suffix == ".txt" for p in out.iterdir())


def test_ext_still_wins_over_the_input_kind(invoke, tmp_path):
    f = write(tmp_path, "t.csv", TICKETS)
    out = tmp_path / "sorted"
    invoke(jroute, ["--csv", "--ext", ".out", "bug:something is broken", "feature:a request", "-i", f, "-o", str(out)])
    assert all(p.suffix == ".out" for p in out.iterdir())


# --------------------------------------------------------------------- jgate


def test_gate_passthrough_copies_the_header_too(invoke, tmp_path):
    f = write(tmp_path, "t.csv", TICKETS)
    res = invoke(jgate, ["--each", "--csv", "-P", "a bug", f])
    assert res.code == 0, res.err
    rows = list(csv.reader(io.StringIO(res.out)))
    assert rows[0] == HEADER
    assert len(rows) == 4, "-P copies the input through, every row of it"


def test_structured_needs_a_per_line_mode(invoke, tmp_path):
    """Without --each/--all the whole file is one state; a header row is part of that document."""
    f = write(tmp_path, "t.csv", TICKETS)
    res = invoke(jgate, ["--csv", "a bug", f])
    assert res.code == 2
    assert "--each" in res.err


# --------------------------------------------------------------------- jwatch


def test_jwatch_alerts_on_a_row_not_on_the_header(invoke, tmp_path):
    f = write(tmp_path, "t.csv", TICKETS)
    res = invoke(jwatch, ["--csv", "a bug", f])
    assert res.lines == ["1,the checkout is broken"], res.out


def test_exec_receives_the_whole_row(invoke, tmp_path):
    f = write(tmp_path, "t.csv", TICKETS)
    marker = tmp_path / "seen.txt"
    res = invoke(jwatch, ["--csv", "a bug", "--exec", f"printf '%s' {{}} > {marker}", f])
    assert res.code == 0, res.err
    assert marker.read_text() == "1,the checkout is broken"


# --------------------------------------------------------------------- shared rules


TOOLS_WITH_FIELD = [
    pytest.param(jsort, ["a bug"], id="jsort"),
    pytest.param(jhead, ["2", "a bug"], id="jhead"),
    pytest.param(jpick, ["a bug"], id="jpick"),
    pytest.param(juniq, ["the same problem"], id="juniq"),
    pytest.param(jtag, ["--labels", "bug,feature"], id="jtag"),
    pytest.param(jroute, ["bug:broken", "feature:a request", "--no-files"], id="jroute"),
    pytest.param(jwatch, ["a bug"], id="jwatch"),
    pytest.param(jmatch, ["a.csv", "b.csv", "x"], id="jmatch"),
    pytest.param(jgrep, ["a bug"], id="jgrep"),
]


@pytest.mark.parametrize(("tool", "argv"), TOOLS_WITH_FIELD)
def test_field_needs_a_structured_mode_everywhere(invoke, tool, argv):
    assert invoke(tool, ["--field", "note", *argv]).code == 2


@pytest.mark.parametrize(("tool", "argv"), TOOLS_WITH_FIELD)
def test_jsonl_and_csv_contradict_each_other(invoke, tool, argv):
    assert invoke(tool, ["--jsonl", "--csv", *argv]).code == 2


@pytest.mark.parametrize(("tool", "argv"), JUDGES_EVERY_ROW)
def test_a_missing_column_is_reported_not_guessed(invoke, tmp_path, tool, argv):
    f = write(tmp_path, "t.csv", TICKETS)
    res = invoke(tool, ["--csv", "--field", "nope", *argv, f])
    assert res.code == 2, res.out
    assert "nope" in res.err


def test_a_dotted_path_reaches_a_nested_field(invoke, tmp_path):
    f = write(tmp_path, "t.jsonl", '{"id": 1, "user": {"note": "the checkout is broken"}}\n')
    res = invoke(jsort, ["--jsonl", "--field", "user.note", "a bug", f])
    assert res.code == 0, res.err
    assert res.mock.bodies[0]["state"] == "the checkout is broken"
