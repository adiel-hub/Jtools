"""Records from lines, paragraphs, whole files, JSONL and CSV; discovery; streaming."""

import io
import sys
import threading
import time

from jevcore.inputs import InputError, Record, discover, get_field, iter_records
from tests.conftest import write


def records(*a, **kw):
    return [r for r in iter_records(*a, **kw) if isinstance(r, Record)]


def test_lines_keep_numbers_and_sources(tmp_path):
    a = write(tmp_path, "a.txt", "one\ntwo\n\nfour\n")
    b = write(tmp_path, "b.txt", "five")
    rs = records([a, b])
    assert [(r.seq, r.source, r.lineno, r.text, r.input_id) for r in rs] == [
        (0, a, 1, "one", 0),
        (1, a, 2, "two", 0),
        (2, a, 3, "", 0),
        (3, a, 4, "four", 0),
        (4, b, 1, "five", 1),
    ]
    assert records([a], keep_blank=False)[2].text == "four"
    assert rs[2].is_blank()


def test_crlf_and_truncation(tmp_path):
    a = write(tmp_path, "a.txt", "one\r\n" + "x" * 50 + "\n")
    rs = records([a], max_chars=10)
    assert rs[0].text == "one" and not rs[0].truncated
    assert rs[1].text == "x" * 10 and rs[1].truncated and rs[1].original == "x" * 50


def test_paragraphs_and_whole(tmp_path):
    a = write(tmp_path, "a.txt", "p1 l1\np1 l2\n\n\np2\n")
    rs = records([a], mode="para")
    assert [(r.lineno, r.text) for r in rs] == [(1, "p1 l1\np1 l2"), (5, "p2")]
    rs = records([a], mode="whole")
    assert len(rs) == 1 and rs[0].text == "p1 l1\np1 l2\n\n\np2\n"


def test_jsonl_field_with_dotted_paths_and_errors(tmp_path):
    a = write(
        tmp_path,
        "a.jsonl",
        '{"event":{"message":"hello"},"n":1}\nnot json\n{"other":1}\n\n{"event":{"message":null}}\n',
    )
    items = list(iter_records([a], jsonl_field="event.message"))
    recs = [i for i in items if isinstance(i, Record)]
    errs = [i for i in items if isinstance(i, InputError)]
    assert [r.text for r in recs] == ["hello", ""]
    assert recs[0].original.startswith('{"event"') and recs[0].data["n"] == 1
    assert len(errs) == 2 and "invalid JSON" in errs[0].message and "no field" in errs[1].message
    assert get_field({"a": [{"b": 2}]}, "a.0.b") == 2
    assert get_field({"a.b": 1, "a": {"b": 2}}, "a.b") == 1


def test_csv_field_keeps_rows_and_header(tmp_path):
    a = write(tmp_path, "a.csv", 'id,text\n1,"hello, world"\n2,"multi\nline"\n3\n')
    items = list(iter_records([a], csv_field="text"))
    recs = [i for i in items if isinstance(i, Record)]
    errs = [i for i in items if isinstance(i, InputError)]
    assert [r.text for r in recs] == ["hello, world", "multi\nline"]
    assert (
        recs[0].original == '1,"hello, world"'
        and recs[0].header == "id,text"
        and recs[0].data == {"id": "1", "text": "hello, world"}
    )
    assert recs[1].lineno == 3
    assert len(errs) == 1 and "columns" in errs[0].message
    bad = list(iter_records([a], csv_field="nope"))
    assert isinstance(bad[0], InputError) and "no column" in bad[0].message


def test_missing_file_is_reported_and_others_continue(tmp_path):
    a = write(tmp_path, "a.txt", "x\n")
    items = list(iter_records([str(tmp_path / "missing"), a]))
    assert isinstance(items[0], InputError) and isinstance(items[1], Record)


def test_stdin_streams_lines_as_they_arrive(monkeypatch):
    r, w = io.StringIO(), None  # placeholder to keep names obvious

    class Slow:
        """A stream whose second line arrives late; the first must be yielded before it."""

        def __init__(self):
            self.n = 0
            self.gate = threading.Event()

        def readline(self):
            self.n += 1
            if self.n == 1:
                return "first\n"
            if self.n == 2:
                self.gate.wait(2)
                return "second\n"
            return ""

    slow = Slow()
    monkeypatch.setattr(sys, "stdin", slow)
    it = iter_records(None)
    t0 = time.perf_counter()
    first = next(it)
    assert isinstance(first, Record) and first.text == "first" and time.perf_counter() - t0 < 1.0
    slow.gate.set()
    assert next(it).text == "second"
    del r, w


def test_stop_event_releases_the_reader(tmp_path):
    a = write(tmp_path, "a.txt", "".join(f"{i}\n" for i in range(1000)))
    stop = threading.Event()
    out = []
    for item in iter_records([a], stop=stop):
        out.append(item)
        if len(out) == 3:
            stop.set()
    assert len(out) == 3


def test_discover_walks_sorted_skips_junk_and_binaries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("b")
    (tmp_path / "src" / "a.py").write_text("a")
    (tmp_path / "src" / "blob.bin").write_bytes(b"\x00\x01")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "m.js").write_text("x")
    (tmp_path / ".hidden.txt").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    files, errors = discover(["."], recursive=True)
    assert files == ["./notes.txt", "./src/a.py", "./src/b.py"] and errors == []
    files, _ = discover(["."], recursive=True, globs=["*.py"])
    assert files == ["./src/a.py", "./src/b.py"]
    files, _ = discover(["."], recursive=True, excludes=["src"])
    assert files == ["./notes.txt"]
    files, _ = discover(["."], recursive=True, hidden=True)
    assert "./.hidden.txt" in files and not any(".git/" in f for f in files)
    files, errors = discover(["src"], recursive=False)
    assert files == [] and "is a directory" in errors[0]
    files, errors = discover(["nope.txt", "-"], recursive=False)
    assert files == ["-"] and "no such file" in errors[0]
