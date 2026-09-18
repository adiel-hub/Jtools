"""jhead: the N most relevant lines, in their original order.  [primitive: score]

    cat long-thread.txt | jhead 5 "the key decisions made"
    dmesg | jhead 10 "hardware errors"

Like ``head``, but relevance decides which lines survive, not position. jsort ranks everything;
jhead keeps input order and cuts to N (``--reorder`` sorts the survivors by score).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import IO

import httpx

from jevcore import rubric
from jevcore.cli import EXIT_NOMATCH, EXIT_OK, Parser, Run, build_parser, cli_entry, dry_run, execute, partial
from jevcore.errors import UsageError
from jevcore.inputs import iter_records
from jevcore.questions import Score

from ._shared import add_levels_option, read_all, render_scored, score_json, score_records

PROG = "jhead"


def parser() -> Parser:
    ap = build_parser(
        PROG,
        "Print the N lines most relevant to a description, keeping their original order.",
        [
            'cat long-thread.txt | jhead 5 "the key decisions made"',
            'dmesg | jhead 10 "hardware errors"',
            'jhead 3 "action items" notes.txt --reorder --with-score',
        ],
        usage="jhead [options] N DESCRIPTION [FILE ...]",
        threshold=None,
    )
    ap.add_argument("--tail", action="store_true", help="the N least relevant lines instead")
    ap.add_argument("--reorder", action="store_true", help="sort the survivors by score instead of input order")
    ap.add_argument("-s", "--with-score", action="store_true", help="prefix each line with its 0-1 score")
    add_levels_option(ap)
    return ap


def prepare(args: argparse.Namespace) -> None:
    positionals = list(args.files)
    if len(positionals) < 2:
        raise UsageError("usage: jhead N DESCRIPTION [FILE ...]")
    try:
        args.count = int(positionals[0])
    except ValueError:
        raise UsageError(f"N must be an integer, got {positionals[0]!r}") from None
    if args.count < 0:
        raise UsageError("N must be 0 or more")
    args.description = positionals[1]
    args.files = positionals[2:]
    args.rubric = rubric.parse_levels(args.levels) if args.levels else None


def question(args: argparse.Namespace) -> Score:
    return rubric.fit_score(args.description, args.rubric)


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    sample = (r.text for r in iter_records(args.files or None, keep_blank=False) if hasattr(r, "text"))
    return dry_run(
        PROG,
        args,
        {"fit": question(args)},
        sample,
        out,
        note=f"one call per line; the {args.count} best scores survive",
    )


async def run(r: Run) -> int:
    args = r.args
    records = await read_all(r, args.files)
    if not records:
        return EXIT_NOMATCH
    if args.count == 0:
        return EXIT_OK
    scores = await score_records(r, records, question(args))
    judged = [rec for rec in records if scores[rec.seq] is not None]
    unjudged = len(records) - len(judged)

    def value(seq: int) -> float:
        answer = scores[seq]
        assert answer is not None
        return answer.normalized

    ranked = sorted(judged, key=lambda rec: ((value(rec.seq) if args.tail else -value(rec.seq)), rec.seq))
    survivors = ranked[: args.count]
    if not args.reorder:
        survivors.sort(key=lambda rec: rec.seq)
    for rank, record in enumerate(survivors, 1):
        answer = scores[record.seq]
        if args.json:
            r.out.json(score_json(record, answer, rank=rank))
        else:
            render_scored(r, record, answer, args.with_score)
    if unjudged:
        r.warn(f"{unjudged:,} of {len(records):,} lines could not be judged and were not considered")
    return partial(EXIT_OK if survivors else EXIT_NOMATCH, unjudged)


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    return execute(PROG, parser(), argv, prepare, run, transport=transport, out=out, err=err, dry=dry)


def cli() -> None:
    cli_entry(main)


if __name__ == "__main__":
    cli()
