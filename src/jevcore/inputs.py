"""Records: what the tools read. Lines by default, or paragraphs, whole files, JSONL and CSV fields.

Everything is a generator so ``tail -f`` works: a line is yielded the moment it arrives, and the
``stop`` event lets a tool that has seen enough (``-m``, ``-q``) release a reader blocked on a pipe.
"""

from __future__ import annotations

import csv
import fnmatch
import io
import json
import os
import sys
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

STDIN = "(standard input)"
DEFAULT_MAX_CHARS = 8000
SKIP_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache"})


@dataclass(slots=True)
class Record:
    seq: int
    """Position in the run, from 0. Output order is restored with it."""
    text: str
    """What Jev is shown (already cut to ``max_chars``)."""
    source: str = STDIN
    lineno: int = 0
    """1-based physical line where the record starts."""
    input_id: int = 0
    """Which input occurrence produced it; repeated paths are separate occurrences."""
    original: str | None = None
    """The full record when only a field was judged (JSONL line, CSV row)."""
    data: Any = None
    """The parsed record for structured input."""
    header: str | None = None
    """CSV header line, so matching rows can be written back with it."""
    truncated: bool = False
    before: tuple[str, ...] = ()
    """Neighbouring records shown to Jev as context (jgrep -C); never printed."""
    after: tuple[str, ...] = ()

    @property
    def shown(self) -> str:
        """What to print for this record: the original when there is one."""
        return self.original if self.original is not None else self.text

    def is_blank(self) -> bool:
        return not self.text.strip()


@dataclass(frozen=True, slots=True)
class InputError:
    message: str


Item = Record | InputError


def _clip(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def open_text(path: str, *, newline: str = "\n") -> TextIO:
    """Open an input. ``utf-8-sig`` drops the byte-order mark every spreadsheet export starts with,
    which otherwise becomes part of the first CSV column name and makes it unaddressable.

    ``newline="\n"`` stops a lone carriage return from ending a line: progress-bar output and some
    container logs are full of them, and universal newlines would split one line into several with
    line numbers that match nothing. CRLF is still handled, by stripping it. The CSV reader needs
    ``newline=""`` instead, because it has to see the terminators inside quoted fields itself.
    """
    if path == "-":
        return sys.stdin
    return open(path, encoding="utf-8-sig", errors="replace", newline=newline)  # the caller closes it


def _readlines(stream: TextIO) -> Iterator[str]:
    """Lines without their terminator, as they arrive. ``readline`` does not read ahead, so a live
    pipe delivers each line immediately."""
    while True:
        line = stream.readline()
        if not line:
            return
        yield line.rstrip("\r\n")


def get_field(data: Any, field: str) -> Any:
    """``a.b.0`` style lookup. An exact key wins over a dotted path. Raises ``KeyError``."""
    if isinstance(data, dict) and field in data:
        return data[field]
    current = data
    for part in field.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.lstrip("-").isdigit() and -len(current) <= int(part) < len(current):
            current = current[int(part)]
        else:
            raise KeyError(field)
    return current


def field_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def iter_records(
    files: Sequence[str] | None,
    *,
    mode: str = "lines",
    jsonl_field: str | None = None,
    csv_field: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    stop: threading.Event | None = None,
    keep_blank: bool = True,
) -> Iterator[Item]:
    """Yield records from files (``-`` for stdin; none means stdin) in order.

    ``mode`` is ``lines``, ``para`` (blank-line separated) or ``whole`` (one record per file).
    """
    seq = 0
    for input_id, path in enumerate(files or ["-"]):
        if stop is not None and stop.is_set():
            return
        name = STDIN if path == "-" else path
        try:
            # The csv module needs the raw terminators inside quoted fields; everything else wants
            # a lone carriage return left alone. See open_text.
            stream = open_text(path, newline="" if csv_field is not None else "\n")
        except OSError as e:
            yield InputError(f"{name}: {e.strerror or e}")
            continue
        try:
            if jsonl_field is not None:
                producer = _jsonl(stream, name, input_id, jsonl_field, max_chars, seq)
            elif csv_field is not None:
                producer = _csv(stream, name, input_id, csv_field, max_chars, seq)
            elif mode == "whole":
                producer = _whole(stream, name, input_id, max_chars, seq)
            elif mode == "para":
                producer = _paragraphs(stream, name, input_id, max_chars, seq)
            else:
                producer = _lines(stream, name, input_id, max_chars, seq)
            for item in producer:
                if stop is not None and stop.is_set():
                    return
                if isinstance(item, Record):
                    seq = item.seq + 1
                    if not keep_blank and item.is_blank():
                        continue
                yield item
        except UnicodeDecodeError as e:
            yield InputError(f"{name}: {e}")
        finally:
            if stream is not sys.stdin:
                stream.close()


def _lines(stream: TextIO, name: str, input_id: int, max_chars: int, seq: int) -> Iterator[Item]:
    for lineno, raw in enumerate(_readlines(stream), 1):
        text, cut = _clip(raw, max_chars)
        yield Record(seq, text, name, lineno, input_id, original=raw if cut else None, truncated=cut)
        seq += 1


def _paragraphs(stream: TextIO, name: str, input_id: int, max_chars: int, seq: int) -> Iterator[Item]:
    buffer: list[str] = []
    start = 0
    for lineno, raw in enumerate(_readlines(stream), 1):
        if raw.strip():
            if not buffer:
                start = lineno
            buffer.append(raw)
        elif buffer:
            joined = "\n".join(buffer)
            text, cut = _clip(joined, max_chars)
            yield Record(seq, text, name, start, input_id, original=joined, truncated=cut)
            seq += 1
            buffer = []
    if buffer:
        joined = "\n".join(buffer)
        text, cut = _clip(joined, max_chars)
        yield Record(seq, text, name, start, input_id, original=joined, truncated=cut)


def _whole(stream: TextIO, name: str, input_id: int, max_chars: int, seq: int) -> Iterator[Item]:
    content = stream.read()
    text, cut = _clip(content, max_chars)
    yield Record(seq, text, name, 1, input_id, original=content, truncated=cut)


def _jsonl(stream: TextIO, name: str, input_id: int, field: str, max_chars: int, seq: int) -> Iterator[Item]:
    for lineno, raw in enumerate(_readlines(stream), 1):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except ValueError as e:
            yield InputError(f"{name}:{lineno}: invalid JSON: {e}")
            continue
        try:
            value = get_field(data, field)
        except KeyError:
            yield InputError(f"{name}:{lineno}: no field {field!r}")
            continue
        text, cut = _clip(field_text(value), max_chars)
        yield Record(seq, text, name, lineno, input_id, original=raw, data=data, truncated=cut)
        seq += 1


def _csv(stream: TextIO, name: str, input_id: int, field: str, max_chars: int, seq: int) -> Iterator[Item]:
    reader = csv.reader(stream)
    try:
        header = next(reader)
    except StopIteration:
        return
    except csv.Error as e:
        yield InputError(f"{name}: {e}")
        return
    if field not in header:
        yield InputError(f"{name}: no column {field!r} in header {header}")
        return
    if len(set(header)) != len(header):
        yield InputError(f"{name}: duplicate column names in header")
        return
    col = header.index(field)
    header_line = _csv_line(header)
    while True:
        start = reader.line_num + 1
        try:
            row = next(reader)
        except StopIteration:
            return
        except csv.Error as e:
            yield InputError(f"{name}:{start}: {e}")
            continue
        if not row or (len(row) == 1 and not row[0].strip()):
            continue
        if len(row) <= col:
            yield InputError(f"{name}:{start}: row has {len(row)} columns, expected at least {col + 1}")
            continue
        text, cut = _clip(row[col], max_chars)
        data = dict(zip(header, row, strict=False))
        yield Record(
            seq, text, name, start, input_id, original=_csv_line(row), data=data, header=header_line, truncated=cut
        )
        seq += 1


def _csv_line(row: Sequence[str]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(row)
    return buf.getvalue()


# ------------------------------------------------------------------ discovery


def is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(8192)
    except OSError:
        return True


def discover(
    paths: Sequence[str],
    *,
    recursive: bool = False,
    globs: Sequence[str] = (),
    excludes: Sequence[str] = (),
    hidden: bool = False,
) -> tuple[list[str], list[str]]:
    """Expand directories (when recursive) into files, in sorted order. Returns (files, errors).

    Skips VCS and dependency directories, hidden entries unless ``hidden``, binary files, and
    symlinks inside directories. Explicit file arguments are always kept.
    """
    found: list[str] = []
    errors: list[str] = []

    def names(path: str, root: str) -> set[str]:
        """Every spelling a pattern might reasonably use for this path.

        ``jgrep -r --glob 'src/*.py' proj`` reads as "under proj, the Python files in src", so the
        pattern is matched against the path relative to the directory being searched as well as
        against the path relative to the working directory and the bare file name.
        """
        return {os.path.basename(path), path, os.path.relpath(path), os.path.relpath(path, root)}

    def matches(path: str, root: str, patterns: Sequence[str]) -> bool:
        return any(fnmatch.fnmatchcase(name, p) for name in names(path, root) for p in patterns)

    def included(path: str, root: str) -> bool:
        if matches(path, root, excludes):
            return False
        return not globs or matches(path, root, globs)

    def walk(root: str, top: str) -> None:
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError as e:
            errors.append(f"{root}: {e.strerror or e}")
            return
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.name in SKIP_DIRS or (not hidden and entry.name.startswith(".")):
                continue
            path = os.path.join(root, entry.name)
            if entry.is_dir(follow_symlinks=False):
                if matches(path, top, excludes):
                    continue
                walk(path, top)
            elif entry.is_file(follow_symlinks=False) and included(path, top) and not is_binary(Path(path)):
                found.append(path)

    for raw in paths or (["."] if recursive else []):
        if raw == "-":
            found.append("-")
            continue
        p = Path(raw)
        if p.is_dir():
            if recursive:
                walk(raw, raw)
            else:
                errors.append(f"{raw}: is a directory (use -r)")
        elif p.exists():
            found.append(raw)
        else:
            errors.append(f"{raw}: no such file")
    return found, errors
