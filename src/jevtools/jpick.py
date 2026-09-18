"""jpick: choose the single best line for a description.  [primitive: choice]

    ls candidates/*.pdf | jpick "best fit for a senior backend role"
    cat subject-lines.txt | jpick "most likely to get opened" --why

jsort ranks every line; jpick returns exactly one (or ``--top N``). Lines are compared *against
each other*: candidates are shown to Jev in groups (``--group``, default 12) as one JSON array
and a choice question picks the best of each group. Group winners meet in the next round until
one group remains; its distribution is the final ranking. For 1,000 lines that is about 90 calls.
"""

from __future__ import annotations

import argparse
import math
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

from ._shared import read_all, split_description

PROG = "jpick"
DEFAULT_GROUP = 12
MAX_GROUP = 40


@dataclass(slots=True)
class Finalist:
    record: Record
    probability: float | None
    runner_up: Record | None = None
    runner_probability: float | None = None


def parser() -> Parser:
    ap = build_parser(
        PROG,
        "Print the one line that best fits a description (or the N best with --top).",
        [
            'ls candidates/*.pdf | jpick "best fit for a senior backend role"',
            'cat subject-lines.txt | jpick "most likely to get opened" --why',
            'cat ideas.txt | jpick "cheapest to build this week" --top 3 --with-score',
        ],
        usage="jpick [options] DESCRIPTION [FILE ...]",
        threshold=None,
    )
    ap.add_argument("--top", type=int, default=1, metavar="N", help="return the N best instead of one (default 1)")
    ap.add_argument(
        "--why",
        action="store_true",
        help="append the winning probability and the runner-up, taken from Jev's distribution",
    )
    ap.add_argument("-s", "--with-score", action="store_true", help="prefix each line with its final-round probability")
    ap.add_argument(
        "--group",
        type=int,
        default=DEFAULT_GROUP,
        metavar="K",
        help=f"candidates compared per call (default {DEFAULT_GROUP}, max {MAX_GROUP})",
    )
    return ap


def prepare(args: argparse.Namespace) -> None:
    args.description, args.files = split_description(args.files)
    if args.top < 1:
        raise UsageError("--top takes 1 or more")
    if not 2 <= args.group <= MAX_GROUP:
        raise UsageError(f"--group must be between 2 and {MAX_GROUP}")
    if args.top > args.group:
        raise UsageError("--top cannot exceed --group (raise --group)")


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    ids = rubric.ids_for(args.group)
    sample = (r.text for r in iter_records(args.files or None, keep_blank=False) if hasattr(r, "text"))
    return dry_run(
        PROG,
        args,
        {"best": rubric.pick(args.description, ids)},
        sample,
        out,
        note=f"candidates are sent {args.group} at a time as a JSON array of {{id, text}}; "
        "group winners advance until one group remains",
    )


def chunks(items: list[Record], size: int) -> list[list[Record]]:
    """Balanced groups: 13 items with size 12 become 7 + 6, not 12 + 1."""
    n = math.ceil(len(items) / size)
    base, extra = divmod(len(items), n)
    out, start = [], 0
    for i in range(n):
        end = start + base + (1 if i < extra else 0)
        out.append(items[start:end])
        start = end
    return out


async def judge_group(run: Run, description: str, group: list[Record]) -> list[tuple[Record, float]] | None:
    """Rank one group by Jev's distribution. ``None`` when the call failed (fail-open)."""
    ids = rubric.ids_for(len(group))
    state = rubric.candidates_state([rec.text for rec in group], ids)
    answers = await run.judge(state, {"best": rubric.pick(description, ids)})
    if not answers:
        return None
    answer = answers["best"]
    assert isinstance(answer, ChoiceAnswer)
    by_id = dict(zip(ids, group, strict=True))
    ranked = [(by_id[cid], p) for cid, p in answer.ranked() if cid in by_id]
    # A stable order for ties: Jev's rounding often gives several candidates 0.
    ranked.sort(key=lambda rp: (-rp[1], rp[0].seq))
    return ranked


async def tournament(
    run: Run, description: str, records: list[Record], top: int, group: int
) -> tuple[list[Finalist], int]:
    """Returns (finalists, failed_calls)."""
    failed = 0
    items = list(records)
    if len(items) == 1:
        return [Finalist(items[0], None)], 0
    while True:
        groups = chunks(items, group) if len(items) > group else [items]
        results = []
        for g in groups:
            results.append(await judge_group(run, description, g))
        if len(groups) == 1:
            ranked = results[0]
            if ranked is None:
                failed += 1
                return [Finalist(rec, None) for rec in items[:top]], failed
            finalists = []
            for i, (rec, p) in enumerate(ranked[:top]):
                nxt = ranked[i + 1] if i + 1 < len(ranked) else None
                finalists.append(Finalist(rec, p, nxt[0] if nxt else None, nxt[1] if nxt else None))
            return finalists, failed
        survivors: list[Record] = []
        eliminated = False
        for g, ranked in zip(groups, results, strict=True):
            if ranked is None:
                failed += 1
                survivors.extend(g)  # cannot judge: nobody from this group is dropped
            else:
                keep = min(top, len(g))
                survivors.extend(rec for rec, _ in ranked[:keep])
                eliminated = eliminated or keep < len(g)
        survivors.sort(key=lambda rec: rec.seq)
        if not eliminated:
            # Every call failed; another round would loop forever. Fall back to input order.
            return [Finalist(rec, None) for rec in survivors[:top]], failed
        items = survivors


def snippet(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def run(r: Run) -> int:
    args = r.args
    records = await read_all(r, args.files)
    if not records:
        return EXIT_NOMATCH
    finalists, failed = await tournament(r, args.description, records, args.top, args.group)
    for rank, f in enumerate(finalists, 1):
        if args.json:
            r.out.json(
                {
                    "rank": rank,
                    "line": f.record.shown,
                    "probability": f.probability,
                    "runner_up": f.runner_up.shown if f.runner_up else None,
                    "runner_up_probability": f.runner_probability,
                    "source": f.record.source,
                    "lineno": f.record.lineno,
                }
            )
            continue
        text = f.record.shown
        if args.why:
            why = f"p={fmt_p(f.probability, 2)}"
            if f.runner_up is not None:
                why += f" vs {fmt_p(f.runner_probability, 2)} for “{snippet(f.runner_up.shown)}”"
            text = f"{text}\t# {why}"
        if args.with_score:
            r.out.scored(f.probability, text)
        else:
            r.out.write(text)
    if failed:
        r.warn(f"{failed:,} comparison call(s) failed; affected groups were not narrowed")
    return partial(EXIT_OK, failed)


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
