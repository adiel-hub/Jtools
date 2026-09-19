"""juniq: drop lines that mean the same as an earlier line.  [primitive: noul over pairs]

    cat feature-requests.txt | juniq "same underlying request"
    sort bugs.txt | juniq "duplicate bug report" --show-groups
    cat titles.txt | juniq -c

Like ``uniq``, but "the same" is a judgment. Exact repeats (ignoring case and spacing) are dropped
for free. Every other line is compared, in ONE call, against the previous ``--window`` *distinct*
lines -- a thousand copies of one line spend none of the window, so what a line is compared with
is a thousand copies' worth of variety rather than a thousand copies. The state is
``{"candidate": line, "kept": [...]}`` and one yes/no question per earlier line.
All lines are judged concurrently; verdicts are then resolved in input order, so a line that is
itself a duplicate can still pull later lines into its group. The first occurrence is kept.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Sequence
from typing import IO

import httpx

from jevcore import rubric
from jevcore.cli import EXIT_NOMATCH, EXIT_OK, Parser, Run, build_parser, cli_entry, dry_run, execute, partial
from jevcore.errors import UsageError
from jevcore.inputs import Record, iter_records
from jevcore.io import DIM, RESET
from jevcore.questions import NoulAnswer

from ._shared import read_all, split_optional_description, trace

PROG = "juniq"
DEFAULT_DESCRIPTION = "the same thing said in different words"
DEFAULT_WINDOW = 50
MAX_WINDOW = 200
_SPACE = re.compile(r"\s+")


def parser() -> Parser:
    ap = build_parser(
        PROG,
        "Remove lines that mean the same as an earlier line, even when worded differently.",
        [
            'cat feature-requests.txt | juniq "same underlying request"',
            'sort bugs.txt | juniq "duplicate bug report" --show-groups',
            "cat headlines.txt | juniq -c | sort -rn | head",
        ],
        usage="juniq [options] [DESCRIPTION] [FILE ...]",
    )
    ap.add_argument(
        "-w",
        "--window",
        type=int,
        default=DEFAULT_WINDOW,
        metavar="N",
        help=f"compare each line with the previous N distinct lines (default {DEFAULT_WINDOW}, max {MAX_WINDOW})",
    )
    ap.add_argument(
        "--show-groups", action="store_true", help="print each kept line followed by its duplicates, indented"
    )
    ap.add_argument("-c", "--count", action="store_true", help="prefix each kept line with the size of its group")
    ap.add_argument("-d", "--repeated", action="store_true", help="print only lines that had duplicates")
    ap.add_argument("-u", "--unique", action="store_true", help="print only lines that had no duplicates")
    return ap


def prepare(args: argparse.Namespace) -> None:
    args.description, args.files = split_optional_description(args.files, DEFAULT_DESCRIPTION)
    if not 1 <= args.window <= MAX_WINDOW:
        raise UsageError(f"--window must be between 1 and {MAX_WINDOW}")
    if args.repeated and args.unique:
        raise UsageError("-d and -u contradict each other")
    if args.show_groups and (args.repeated or args.unique or args.count):
        raise UsageError("--show-groups cannot be combined with -c, -d or -u")


def normalise(text: str) -> str:
    return _SPACE.sub(" ", text.strip().lower())


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    sample = (r.text for r in iter_records(args.files or None, keep_blank=False) if hasattr(r, "text"))
    qs = {f"k{i}": rubric.same_meaning(args.description, i) for i in range(2)}
    return dry_run(
        PROG,
        args,
        qs,
        sample,
        out,
        note=f"one call per line with up to {args.window} pair questions; exact repeats are free",
    )


async def run(r: Run) -> int:
    args = r.args
    records = await read_all(r, args.files)
    if not records:
        return r.empty_input()
    by_seq = {rec.seq: rec for rec in records}

    # Free pass: exact repeats.
    first_by_norm: dict[str, int] = {}
    exact_dup_of: dict[int, int] = {}
    unique: list[Record] = []
    for rec in records:
        if rec.truncated:
            # Two different long lines share their first --max-chars characters, and the text this
            # sees is already cut. Only the model can say whether they mean the same thing, so a
            # cut line is never folded away for free.
            unique.append(rec)
            continue
        key = normalise(rec.text)
        if key in first_by_norm:
            exact_dup_of[rec.seq] = first_by_norm[key]
        else:
            first_by_norm[key] = rec.seq
            unique.append(rec)

    # One request per unique line against its window of earlier unique lines, all concurrent.
    async def compare(i: int) -> list[float] | None:
        window = unique[max(0, i - args.window) : i]
        if not window:
            trace(r, "first", unique[i])  # nothing to compare it with; it is kept by definition
            return []
        state = {"candidate": unique[i].text, "kept": [rec.text for rec in window]}
        questions = {f"k{j}": rubric.same_meaning(args.description, j) for j in range(len(window))}
        answers = await r.judge(state, questions)
        if answers is None:
            trace(r, "-", unique[i])
            return None
        out = []
        for j in range(len(window)):
            a = answers[f"k{j}"]
            out.append(a.probability if isinstance(a, NoulAnswer) else 0.0)
        trace(r, f"max p={max(out, default=0.0):.2f}", unique[i])
        return out

    verdicts = await asyncio.gather(*(compare(i) for i in range(len(unique))))

    # Resolve in order. rep[seq] is the kept line a duplicate belongs to.
    rep: dict[int, int] = {}
    groups: dict[int, list[Record]] = {}
    unjudged = 0
    for i, rec in enumerate(unique):
        probs = verdicts[i]
        window = unique[max(0, i - args.window) : i]
        target: int | None = None
        if probs is None:
            unjudged += 1  # fail open: keep the line rather than drop data on an error
        elif probs:
            best_j, best_p = max(enumerate(probs), key=lambda jp: (jp[1], -jp[0]))
            if best_p >= args.threshold:
                target = rep[window[best_j].seq]
        if target is None:
            rep[rec.seq] = rec.seq
            groups[rec.seq] = [rec]
        else:
            rep[rec.seq] = target
            groups[target].append(rec)
    for seq, orig in exact_dup_of.items():
        target = rep[orig]
        groups[target].append(by_seq[seq])
        rep[seq] = target
    for members in groups.values():
        members.sort(key=lambda x: x.seq)

    kept = [rec for rec in records if rep.get(rec.seq) == rec.seq]
    printed = 0
    for rec in kept:
        members = groups[rec.seq]
        dups = members[1:]
        if args.repeated and not dups:
            continue
        if args.unique and dups:
            continue
        printed += 1
        if args.json:
            r.out.json(
                {
                    "line": rec.shown,
                    "count": len(members),
                    "duplicates": [d.shown for d in dups],
                    "source": rec.source,
                    "lineno": rec.lineno,
                }
            )
        elif args.show_groups:
            r.out.write(rec.shown)
            for d in dups:
                text = f"  ↳ {d.shown}"
                r.out.write(f"{DIM}{text}{RESET}" if r.out.colour else text)
        elif args.count:
            r.out.write(f"{len(members):>7} {rec.shown}")
        else:
            r.out.write(rec.shown)
    if cut := sum(1 for rec in records if rec.truncated):
        # Worth saying: two different lines can look alike once they are cut to the same length.
        r.note(f"{cut:,} line(s) were compared on their first {args.max_chars:,} characters; raise --max-chars")
    if unjudged:
        r.note(f"{unjudged:,} line(s) could not be compared and were kept")
    # -d and -u are filters; printing nothing is "no match", as it is for every other tool.
    return partial(EXIT_OK if printed else EXIT_NOMATCH, unjudged)


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
