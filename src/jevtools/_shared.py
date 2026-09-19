"""Helpers several tools share: positional parsing, scoring every record, rendering."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from typing import Any

from jevcore.cli import Run
from jevcore.errors import UsageError
from jevcore.inputs import InputError, Record, iter_records
from jevcore.pipeline import collect
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
        run.warn(error.message)

    return _report


async def read_all(run: Run, files: Sequence[str], *, keep_blank: bool = False, mode: str = "lines") -> list[Record]:
    return await collect(
        iter_records(files or None, mode=mode, max_chars=run.args.max_chars, keep_blank=keep_blank),
        report_input_error(run),
    )


async def score_records(run: Run, records: Sequence[Record], question: Score) -> dict[int, ScoreAnswer | None]:
    """Score every record with one question, concurrently. ``None`` where judging failed (fail-open)."""

    async def one(record: Record) -> tuple[int, ScoreAnswer | None]:
        answers = await run.judge(record.text, {"fit": question})
        answer = answers.get("fit") if answers else None
        return record.seq, answer if isinstance(answer, ScoreAnswer) else None

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


def render_scored(run: Run, record: Record, answer: ScoreAnswer | None, with_score: bool) -> None:
    if with_score:
        run.out.scored(answer.normalized if answer else None, record.shown)
    else:
        run.out.write(record.shown)


def add_levels_option(ap: argparse.ArgumentParser) -> None:
    ap.add_argument(
        "--levels",
        metavar="CSV",
        help='your own ordered rubric, lowest first, e.g. "not at all,somewhat,very" (default: a 5-rung fit scale)',
    )
