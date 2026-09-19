"""jmatch: semantic join.  [primitive: choice]

    jmatch customers.csv crm-records.csv "same company"
    jmatch invoices.txt payments.txt "same transaction" --unmatched
    jmatch questions.txt faq.txt "the FAQ entry that answers the question" --format "{a} => {b} ({score})"

For each line in FILE_A, find the line in FILE_B that goes with it. Candidates from B are shown
to Jev in groups (``--group``, default 12) as ``{"target": a, "candidates": [{id, text}]}`` with a
choice question that includes a "none" option; group winners meet in a final round. Lines in A
whose best candidate is "none" or below the threshold are unmatched (printed with ``--unmatched``).
A cheap word-overlap ``--shortlist`` keeps the candidate set small for large B files.

``--csv`` / ``--jsonl`` read both files as records, which is what a join of two exports usually
is: the header row stops being a candidate, and each side goes to Jev as the object it is, so one
description can weigh several columns at once. Joined files rarely name their columns alike, so
``--field`` speaks for FILE_A and ``--field-b`` for FILE_B.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import IO

import httpx

from jevcore import rubric
from jevcore.cli import EXIT_NOMATCH, EXIT_OK, Parser, Run, build_parser, cli_entry, dry_run, execute, partial
from jevcore.errors import UsageError
from jevcore.inputs import Record, iter_records
from jevcore.io import fmt_p
from jevcore.questions import ChoiceAnswer

from ._shared import add_structured, prepare_structured, read_all, structured_kwargs, trace, unescape

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
    add_structured(ap)
    ap.add_argument("--field-b", metavar="NAME", help="the field or column to judge in FILE_B, when it differs")
    return ap


def prepare(args: argparse.Namespace) -> None:
    prepare_structured(args)
    if args.field_b and not args.structured:
        raise UsageError("--field-b requires --jsonl or --csv")
    if len(args.positionals) != 3:
        raise UsageError("usage: jmatch FILE_A FILE_B DESCRIPTION")
    args.file_a, args.file_b, args.description = args.positionals
    if args.file_a == "-" and args.file_b == "-":
        raise UsageError("only one of the files can be standard input")
    if not 2 <= args.group <= MAX_GROUP:
        raise UsageError(f"--group must be between 2 and {MAX_GROUP}")
    if args.shortlist is not None and args.shortlist < 1:
        raise UsageError("--shortlist takes 1 or more")
    args.format = unescape(args.format)
    # Validate with exactly the values the run will pass. b_line is an int for a hit and "" for a
    # miss, so "{b_line:03d}" used to pass validation and then die halfway through the output; but
    # the miss row is only ever printed with --unmatched, and never under --json, so a format that
    # is fine for this run must not be rejected for a row this run cannot reach.
    samples = [{"b": "b", "b_line": 0}]
    if args.unmatched and not args.json:
        samples.append({"b": "", "b_line": ""})
    for sample in samples:
        try:
            args.format.format(a="a", score="0.80", a_line=1, **sample)
        except (KeyError, IndexError, ValueError) as e:
            raise UsageError(f"--format: {e}; use {{a}} {{b}} {{score}} {{a_line}} {{b_line}}") from None


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    source = iter_records([args.file_a], keep_blank=False, **structured_kwargs(args))
    sample = (r.text for r in source if hasattr(r, "text"))
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
    state = {"target": target.state, "candidates": rubric.candidates_state([rec.state for rec in group], ids)}
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


@dataclass(slots=True)
class Match:
    b: Record | None = None
    """The matching B line, or None when Jev chose "none" (or nothing could be judged)."""
    probability: float | None = None
    """Probability of the chosen B line; None when there is no candidate to score."""
    judged: bool = True
    """False when every call for this A line failed (fail-open)."""
    failed_calls: int = 0


async def best_match(run: Run, a: Record, candidates: list[Record]) -> Match:
    args = run.args
    failed = 0
    items = candidates
    while True:
        groups = chunks(items, args.group) if len(items) > args.group else [items]
        results = await asyncio.gather(*(judge_group(run, args.description, a, g) for g in groups))
        if len(groups) == 1:
            res = results[0]
            if res is None:
                return Match(judged=False, failed_calls=failed + 1)
            b, p = res
            return Match(b, p if b is not None else None, True, failed)
        survivors: list[Record] = []
        for g, res in zip(groups, results, strict=True):
            if res is None:
                failed += 1
                survivors.extend(g)  # keep the whole group when its call failed
            elif res[0] is not None:
                survivors.append(res[0])
        if not survivors:
            return Match(failed_calls=failed)  # every judged group said "none"
        judged_groups = sum(1 for res in results if res is not None)
        if not judged_groups:
            return Match(judged=False, failed_calls=failed)  # every call failed; give up on this line
        if len(survivors) >= len(items):
            # A failed group is re-added whole, so the survivor list can stop shrinking while some
            # groups are still answering. Narrow to what was actually judged rather than give up
            # on a line another group already found a winner for.
            survivors = [res[0] for res in results if res is not None and res[0] is not None]
            if not survivors:
                return Match(failed_calls=failed)
        items = survivors


async def run(r: Run) -> int:
    args = r.args
    a_records = await read_all(r, [args.file_a])
    b_records = await read_all(r, [args.file_b], field=args.field_b)
    if not a_records:
        return r.empty_input(f"{args.file_a}: no lines")
    if not b_records:
        return r.empty_input(f"{args.file_b}: no lines")
    b_words = [words(b.text) for b in b_records] if args.shortlist else []

    async def one(a: Record) -> Match:
        cands = shortlist(a, b_records, b_words, args.shortlist) if args.shortlist else b_records
        m = await best_match(r, a, cands)
        trace(r, "-" if m.b is None else f"{fmt_p(m.probability)} {m.b.lineno}", a)
        return m

    results = await asyncio.gather(*(one(a) for a in a_records))
    matched = 0
    for a, m in zip(a_records, results, strict=True):
        hit = m.b is not None and m.probability is not None and m.probability >= args.threshold
        if hit:
            matched += 1
        b = m.b if hit else None
        score = m.probability if hit else None
        if args.json:
            r.out.json(
                {
                    "a": a.shown,
                    "b": b.shown if b else None,
                    "score": None if score is None else round(score, 4),
                    "matched": hit,
                    "a_line": a.lineno,
                    "b_line": b.lineno if b else None,
                    "judged": m.judged,
                }
            )
        elif b is not None:
            r.out.write(args.format.format(a=a.shown, b=b.shown, score=fmt_p(score), a_line=a.lineno, b_line=b.lineno))
        elif args.unmatched:
            r.out.write(args.format.format(a=a.shown, b="", score=fmt_p(None), a_line=a.lineno, b_line=""))
    unjudged = sum(1 for m in results if not m.judged)
    if unjudged:
        r.note(f"{unjudged:,} line(s) of {args.file_a} could not be judged")
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
