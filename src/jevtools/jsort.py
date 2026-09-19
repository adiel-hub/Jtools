"""jsort: sort lines by how well they fit a description.  [primitive: score]

    cat feedback.txt | jsort "angriest customer first"
    cat tasks.txt    | jsort "most urgent" --asc
    tail -100 sales.log | jsort "highest buying intent" -n 10 --with-score

Every line is rated on a five-rung fit scale in one call per line, all lines concurrently, then
sorted by the interpolated score (ties keep input order). Lines that could not be judged sort
last and make the exit status 5.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import IO

import httpx

from jevcore import rubric
from jevcore.cli import EXIT_OK, Parser, Run, build_parser, cli_entry, dry_run, execute, partial
from jevcore.errors import UsageError
from jevcore.inputs import iter_records
from jevcore.questions import Score

from ._shared import (
    add_levels_option,
    add_structured,
    prepare_structured,
    read_all,
    render_scored,
    score_json,
    score_records,
    split_description,
    structured_kwargs,
    write_header_once,
)

PROG = "jsort"


def parser() -> Parser:
    ap = build_parser(
        PROG,
        "Sort lines by how well they fit a plain-English description, best first.",
        [
            'cat feedback.txt | jsort "angriest customer first"',
            'cat tasks.txt | jsort "most urgent" --asc',
            'tail -100 sales.log | jsort "highest buying intent" -n 10 --with-score',
        ],
        usage="jsort [options] DESCRIPTION [FILE ...]",
        threshold=None,
    )
    ap.add_argument("--asc", action="store_true", help="worst fit first")
    ap.add_argument("-n", "--limit", type=int, metavar="N", help="print only the first N lines of the sorted output")
    ap.add_argument("-s", "--with-score", action="store_true", help="prefix each line with its 0-1 score")
    add_levels_option(ap)
    add_structured(ap)
    return ap


def prepare(args: argparse.Namespace) -> None:
    prepare_structured(args)
    args.description, args.files = split_description(args.files)
    if args.limit is not None and args.limit < 0:
        raise UsageError("-n takes 0 or more lines")
    args.rubric = rubric.parse_levels(args.levels) if args.levels else None


def question(args: argparse.Namespace) -> Score:
    return rubric.fit_score(args.description, args.rubric)


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    source = iter_records(args.files or None, keep_blank=False, **structured_kwargs(args))
    sample = (r.text for r in source if hasattr(r, "text"))
    return dry_run(
        PROG, args, {"fit": question(args)}, sample, out, note="one call per line; sorted by the interpolated score"
    )


async def run(r: Run) -> int:
    args = r.args
    records = await read_all(r, args.files)
    if not records:
        return r.empty_input()
    scores = await score_records(r, records, question(args))
    unjudged = sum(1 for v in scores.values() if v is None)

    def key(seq: int) -> tuple[int, float, int]:
        answer = scores[seq]
        if answer is None:
            return (1, 0.0, seq)  # unjudged always last
        value = answer.normalized
        return (0, value if args.asc else -value, seq)

    ordered = sorted(records, key=lambda rec: key(rec.seq))
    if args.limit is not None:
        ordered = ordered[: args.limit]
    write_header_once(r, records, prefixed=args.with_score)
    for rank, record in enumerate(ordered, 1):
        answer = scores[record.seq]
        if args.json:
            r.out.json(score_json(record, answer, rank=rank))
        else:
            render_scored(r, record, answer, args.with_score)
    if unjudged:
        r.note(f"{unjudged:,} of {len(records):,} lines could not be judged and were sorted last")
    return partial(EXIT_OK, unjudged)


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
