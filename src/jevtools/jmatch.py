"""jmatch: semantic join.  [primitive: choice]

    jmatch customers.csv crm-records.csv "same company"
    jmatch invoices.txt payments.txt "same transaction" --unmatched
    jmatch questions.txt faq.txt "the FAQ entry that answers the question" --format "{a} => {b} ({score})"

For each line in FILE_A, find the line in FILE_B that goes with it. Candidates from B are shown
to Jev in groups (``--group``, default 12) as ``{"target": a, "candidates": [{id, text}]}`` with a
choice question that includes a "none" option; group winners meet in a final round. Lines in A
whose best candidate is "none" or below the threshold are unmatched (printed with ``--unmatched``).
A cheap word-overlap ``--shortlist`` keeps the candidate set small for large B files.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import re
from collections.abc import Sequence
from typing import IO

import httpx

from jevcore import rubric
from jevcore.cli import EXIT_NOMATCH, EXIT_OK, Parser, Run, build_parser, cli_entry, dry_run, execute, partial
from jevcore.errors import UsageError
from jevcore.inputs import Record, iter_records
from jevcore.io import fmt_p
from jevcore.questions import ChoiceAnswer

from ._shared import read_all

PROG = "jmatch"
DEFAULT_GROUP = 12
MAX_GROUP = 40
DEFAULT_FORMAT = "{a}\t{b}\t{score}"
_WORD = re.compile(r"\w+")


def parser() -> Parser:
    ap = build_parser(
        PROG,
        "For each line in FILE_A, print the line in FILE_B that matches it by meaning.",
        [
            'jmatch customers.csv crm-records.csv "same company"',
            'jmatch invoices.txt payments.txt "same transaction" --unmatched',
            'jmatch questions.txt faq.txt "the FAQ entry that answers the question" --format "{a} => {b}"',
        ],
        usage="jmatch [options] FILE_A FILE_B DESCRIPTION",
        files=False,
    )
    ap.add_argument("positionals", nargs="+", metavar="FILE_A FILE_B DESCRIPTION", help=argparse.SUPPRESS)
    ap.add_argument("--unmatched", action="store_true", help="also print lines of FILE_A that matched nothing")
    ap.add_argument(
        "--format",
        default=DEFAULT_FORMAT,
        metavar="FMT",
        help="output template with {a} {b} {score} {a_line} {b_line} (default: a, b, score tab-separated)",
    )
    ap.add_argument(
        "--group",
        type=int,
        default=DEFAULT_GROUP,
        metavar="K",
        help=f"candidates compared per call (default {DEFAULT_GROUP}, max {MAX_GROUP})",
    )
    ap.add_argument(
        "--shortlist",
        type=int,
        metavar="M",
        help="only consider the M candidates sharing the most words with each A line (a free prefilter)",
    )
    return ap


def prepare(args: argparse.Namespace) -> None:
    if len(args.positionals) != 3:
        raise UsageError("usage: jmatch FILE_A FILE_B DESCRIPTION")
    args.file_a, args.file_b, args.description = args.positionals
    if args.file_a == "-" and args.file_b == "-":
        raise UsageError("only one of the files can be standard input")
    if not 2 <= args.group <= MAX_GROUP:
        raise UsageError(f"--group must be between 2 and {MAX_GROUP}")
    if args.shortlist is not None and args.shortlist < 1:
        raise UsageError("--shortlist takes 1 or more")
    args.format = args.format.encode().decode("unicode_escape")
    try:
        args.format.format(a="", b="", score="", a_line=0, b_line=0)
    except (KeyError, IndexError, ValueError) as e:
        raise UsageError(f"--format: {e}; use {{a}} {{b}} {{score}} {{a_line}} {{b_line}}") from None


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    sample = (r.text for r in iter_records([args.file_a], keep_blank=False) if hasattr(r, "text"))
    ids = rubric.ids_for(min(args.group, 3))
    return dry_run(
        PROG,
        args,
        {"match": rubric.match(args.description, ids)},
        sample,
        out,
        note=f'each A line is the "target"; B lines are "candidates", {args.group} per call, with a "none" option',
    )


def words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if len(w) > 1}


def shortlist(a: Record, b_records: list[Record], b_words: list[set[str]], m: int) -> list[Record]:
    wa = words(a.text)
    scored = sorted(range(len(b_records)), key=lambda i: (-len(wa & b_words[i]), i))
    return [b_records[i] for i in scored[:m]]


async def judge_group(
    run: Run, description: str, target: Record, group: list[Record]
) -> tuple[Record | None, float] | None:
    """Best candidate of one group and its probability; (None, p) when 'none' wins; None on a failed call."""
    ids = rubric.ids_for(len(group))
    state = {"target": target.text, "candidates": rubric.candidates_state([rec.text for rec in group], ids)}
    answers = await run.judge(state, {"match": rubric.match(description, ids)})
    if not answers:
        return None
    answer = answers["match"]
    assert isinstance(answer, ChoiceAnswer)
    if answer.choice == rubric.NONE_OPTION:
        return None, answer.probability
    return dict(zip(ids, group, strict=True))[answer.choice], answer.probability


def chunks(items: list[Record], size: int) -> list[list[Record]]:
    n = math.ceil(len(items) / size)
    base, extra = divmod(len(items), n)
    out, start = [], 0
    for i in range(n):
        end = start + base + (1 if i < extra else 0)
        out.append(items[start:end])
        start = end
    return out


async def best_match(run: Run, a: Record, candidates: list[Record]) -> tuple[Record | None, float | None, int]:
    """Returns (b or None, probability or None if nothing could be judged, failed_calls)."""
    args = run.args
    failed = 0
    items = candidates
    while True:
        groups = chunks(items, args.group) if len(items) > args.group else [items]
        results = await asyncio.gather(*(judge_group(run, args.description, a, g) for g in groups))
        if len(groups) == 1:
            res = results[0]
            if res is None:
                return None, None, failed + 1
            b, p = res
            return b, p, failed
        survivors: list[Record] = []
        for g, res in zip(groups, results, strict=True):
            if res is None:
                failed += 1
                survivors.extend(g)  # keep the whole group when its call failed
            elif res[0] is not None:
                survivors.append(res[0])
        if not survivors:
            return None, 0.0, failed
        if len(survivors) >= len(items):
            return None, None, failed  # every call failed; give up on this line
        items = survivors


async def run(r: Run) -> int:
    args = r.args
    a_records, b_records = await read_all(r, [args.file_a]), await read_all(r, [args.file_b])
    if not a_records:
        r.warn(f"{args.file_a}: no lines")
        return EXIT_NOMATCH
    if not b_records:
        r.warn(f"{args.file_b}: no lines")
        return EXIT_NOMATCH
    b_words = [words(b.text) for b in b_records] if args.shortlist else []

    async def one(a: Record) -> tuple[Record | None, float | None, int]:
        cands = shortlist(a, b_records, b_words, args.shortlist) if args.shortlist else b_records
        return await best_match(r, a, cands)

    results = await asyncio.gather(*(one(a) for a in a_records))
    matched = 0
    failed_total = 0
    for a, (b, p, failed) in zip(a_records, results, strict=True):
        failed_total += failed
        hit = b is not None and p is not None and p >= args.threshold
        if hit:
            matched += 1
        if args.json:
            r.out.json(
                {
                    "a": a.shown,
                    "b": b.shown if hit and b else None,
                    "score": None if p is None else round(p, 4),
                    "matched": hit,
                    "a_line": a.lineno,
                    "b_line": b.lineno if hit and b else None,
                    "judged": p is not None,
                }
            )
        elif hit and b is not None:
            r.out.write(args.format.format(a=a.shown, b=b.shown, score=fmt_p(p), a_line=a.lineno, b_line=b.lineno))
        elif args.unmatched:
            r.out.write(
                args.format.format(
                    a=a.shown, b="", score=fmt_p(p if p is not None else None), a_line=a.lineno, b_line=""
                )
            )
    unjudged = sum(1 for _, p, _f in results if p is None)
    if unjudged:
        r.warn(f"{unjudged:,} line(s) of {args.file_a} could not be judged")
    return partial(EXIT_OK if matched else EXIT_NOMATCH, unjudged)


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
