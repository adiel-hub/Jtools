"""Helpers several tools share: positional parsing, scoring every record, rendering."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from collections.abc import Sequence
from typing import Any

from jevcore.cli import Run
from jevcore.errors import UsageError
from jevcore.inputs import InputError, Record, iter_records
from jevcore.pipeline import PipelineResult, collect
from jevcore.questions import Score, ScoreAnswer


def split_description(positionals: Sequence[str], *, what: str = "DESCRIPTION") -> tuple[str, list[str]]:
    """``[DESCRIPTION, FILE...]`` -> (description, files). The description is required."""
    if not positionals:
        raise UsageError(f"missing {what}")
    return positionals[0], list(positionals[1:])


def split_optional_description(positionals: Sequence[str], default: str) -> tuple[str, list[str]]:
    """For tools where the description is optional: a leading argument that is an existing file
    (or ``-``) is an input, anything else is the description."""
    if not positionals:
        return default, []
    first = positionals[0]
    if first == "-" or os.path.exists(first):
        return default, list(positionals)
    return first, list(positionals[1:])


def report_input_error(run: Run) -> Any:
    def _report(error: InputError) -> None:
        run.input_errors += 1
        run.warn(error.message)

    return _report


LARGE_INPUT = 200_000
"""Records above which a tool that holds the whole input says what that costs.

jsort, jpick, jhead, juniq and jmatch cannot stream: ranking needs every record before it can
place the first one. Measured against the mock, that is roughly 6.5 KB per record once the
outstanding requests are counted, so 200,000 lines is well over a gigabyte and a million is not
survivable on most machines. The default budget stops a run near 77,000 lines long before that
matters; `--budget 0` removes the only thing that was standing in the way, so the tool says so
while there is still something the user can do about it.
"""


async def read_all(run: Run, files: Sequence[str], *, keep_blank: bool = False, mode: str = "lines") -> list[Record]:
    records = await collect(
        iter_records(files or None, mode=mode, max_chars=run.args.max_chars, keep_blank=keep_blank),
        report_input_error(run),
    )
    if len(records) >= LARGE_INPUT:
        run.warn(
            f"{len(records):,} records held in memory at once: this tool has to see all of them to answer. "
            "Expect gigabytes; split the input, or pipe it through head, if the machine cannot spare them"
        )
    return records


async def score_records(run: Run, records: Sequence[Record], question: Score) -> dict[int, ScoreAnswer | None]:
    """Score every record with one question, concurrently. ``None`` where judging failed (fail-open)."""

    async def one(record: Record) -> tuple[int, ScoreAnswer | None]:
        answers = await run.judge(record.text, {"fit": question})
        answer = answers.get("fit") if answers else None
        scored = answer if isinstance(answer, ScoreAnswer) else None
        trace(run, "-" if scored is None else f"{scored.normalized:.2f}", record)
        return record.seq, scored

    results = await asyncio.gather(*(one(r) for r in records))
    return dict(results)


def score_json(record: Record, answer: ScoreAnswer | None, **extra: Any) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "line": record.shown,
        "score": round(answer.normalized, 4) if answer else None,
        "level": answer.legend.get(answer.level) if answer else None,
        "confidence": answer.confidence if answer else None,
        "source": record.source,
        "lineno": record.lineno,
    }
    obj.update(extra)
    return obj


def trace(run: Run, verdict: str, record: Record) -> None:
    """``--verbose``: one line on stderr per decision, as it is made. Same shape in every tool."""
    if run.args.verbose:
        run.warn(f"{verdict:<12} {record.text[:70]}")


def render_scored(run: Run, record: Record, answer: ScoreAnswer | None, with_score: bool) -> None:
    if with_score:
        run.out.scored(answer.normalized if answer else None, record.shown)
    else:
        run.out.write(record.shown)


ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "0": "\0", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "\\": "\\"}
_ESCAPE = re.compile(r"\\(.)", re.S)


def unescape(text: str) -> str:
    r"""Turn ``\t``, ``\n`` and the rest of the usual escapes into the characters they name.

    Only those. Decoding the whole string as ``unicode_escape`` is shorter and crashes on
    ``--sep '\u2192'``, on ``--sep '\'``, and on any separator above U+00FF, none of which is a
    reason to stop. Anything this table does not know is left exactly as the user wrote it.
    """
    if "\\" not in text:
        return text
    return _ESCAPE.sub(lambda m: ESCAPES.get(m.group(1), m.group(0)), text)


def report_pipeline_errors(run: Run, result: PipelineResult, limit: int = 3) -> None:
    """Non-API failures inside a judge (bad encoding, a closed cache): say what happened, briefly."""
    for message in result.errors[:limit]:
        run.warn(message)
    if len(result.errors) > limit:
        run.warn(f"and {len(result.errors) - limit:,} more records failed the same way")


def add_levels_option(ap: argparse.ArgumentParser) -> None:
    ap.add_argument(
        "--levels",
        metavar="CSV",
        help='your own ordered rubric, lowest first, e.g. "not at all,somewhat,very" (default: a 5-rung fit scale)',
    )
