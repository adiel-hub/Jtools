"""jtag: annotate every line with a label or a score.  [primitive: choice OR score]

    cat tickets.txt | jtag --labels "bug,feature,question,complaint"
    cat reviews.txt | jtag --score "how positive (0-100)"
    cat leads.csv   | jtag --labels "hot:ready to buy,warm:interested,cold:no intent" --suffix --sep ,

Adds a column; never filters or reorders. Two modes, one tool: ``--labels`` asks a choice
question and writes the winning label; ``--score`` asks a score question and writes a number
(0-1, or rescaled to a range written in the description or given with ``--scale``).
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from typing import IO, Any

import httpx

from jevcore import rubric
from jevcore.cli import EXIT_OK, Parser, Run, build_parser, cli_entry, dry_run, execute, partial
from jevcore.errors import UsageError
from jevcore.inputs import Record, iter_records
from jevcore.io import fmt_p, paint, score_colour
from jevcore.pipeline import Pipeline
from jevcore.questions import Answer, ChoiceAnswer, Question, ScoreAnswer

from ._shared import add_levels_option, report_input_error, report_pipeline_errors, trace, unescape

PROG = "jtag"


def parser() -> Parser:
    ap = build_parser(
        PROG,
        "Add a label column (--labels) or a score column (--score) to every line. Nothing is dropped or moved.",
        [
            'cat tickets.txt | jtag --labels "bug,feature,question,complaint"',
            'cat reviews.txt | jtag --score "how positive (0-100)"',
            'cat leads.txt | jtag --labels "hot:ready to buy,warm:interested,cold:no intent" --with-prob',
        ],
        usage="jtag [options] (--labels CSV | --score DESCRIPTION) [FILE ...]",
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--labels", metavar="CSV", help='labels, optionally described: "bug,feature:new capability,question"'
    )
    mode.add_argument(
        "--label",
        action="append",
        metavar="NAME:DESCRIPTION",
        dest="label_specs",
        help="one label; repeat for each. Use this when a description contains commas",
    )
    mode.add_argument("--score", metavar="DESCRIPTION", help='what to rate, e.g. "how positive (0-100)"')
    ap.add_argument("--sep", default="\t", metavar="CHAR", help="column separator (default: tab)")
    ap.add_argument("--suffix", action="store_true", help="put the new column last instead of first")
    ap.add_argument(
        "--with-prob",
        action="store_true",
        help="also write the probability of the label, or the confidence of the score",
    )
    ap.add_argument(
        "--default", metavar="LABEL", help="labels mode: use this label when the best label's probability is below -p"
    )
    ap.add_argument(
        "--scale",
        metavar="LO-HI",
        help='score mode: rescale the 0-1 score, e.g. "1-5" (default: a range written in the description)',
    )
    add_levels_option(ap)
    return ap


def prepare(args: argparse.Namespace) -> None:
    args.sep = unescape(args.sep)  # allow --sep '\t' as well as --sep '|'
    args.label_mode = bool(args.labels or args.label_specs)
    if args.label_mode:
        args.label_map = (
            rubric.parse_buckets(args.label_specs, what="label")
            if args.label_specs
            else rubric.parse_labels(args.labels)
        )
        if args.default and args.default in args.label_map:
            raise UsageError("--default must name a label that is not in --labels (it marks the undecided)")
        if args.scale or args.levels:
            raise UsageError("--scale and --levels apply to --score mode only")
        if args.default and not re.fullmatch(rubric.NAME, args.default):
            raise UsageError("--default must be a plain label name")
    else:
        if args.default:
            raise UsageError("--default applies to --labels mode only")
        args.rubric = rubric.parse_levels(args.levels) if args.levels else None
        scale = rubric.parse_scale(args.scale, bare=True) if args.scale else rubric.parse_scale(args.score)
        if args.scale and scale is None:
            raise UsageError('--scale must look like "LO-HI", for example 0-100')
        args.range = scale


def question(args: argparse.Namespace) -> Question:
    if args.label_mode:
        return rubric.classify(args.label_map)
    return rubric.fit_score(args.score, args.rubric)


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    sample = (r.text for r in iter_records(args.files or None, keep_blank=False) if hasattr(r, "text"))
    return dry_run(PROG, args, {"tag": question(args)}, sample, out, note="one call per line; output keeps input order")


def format_score(value: float, rng: tuple[float, float] | None) -> str:
    if rng is None:
        return f"{value:.2f}"
    lo, hi = rng
    scaled = lo + value * (hi - lo)
    if abs(hi - lo) >= 10 and float(lo).is_integer() and float(hi).is_integer():
        return str(round(scaled))
    return f"{scaled:.1f}"


def verdict(args: argparse.Namespace, answer: Answer | None) -> str:
    """The one-word decision --verbose shows: the label, or the score on its scale."""
    if isinstance(answer, ChoiceAnswer):
        return answer.choice
    if isinstance(answer, ScoreAnswer):
        return format_score(answer.normalized, args.range)
    return "-"


def render(r: Run, rec: Record, answer: Answer | None) -> None:
    args = r.args
    text = rec.shown
    if rec.is_blank():
        # --json is the machine-readable mode: one object per line, or jq stops at the first blank.
        # No "judged" key: a blank line was never a candidate, so `select(.judged == false)` must
        # keep meaning "this line could not be judged".
        if args.json:
            key = "label" if args.label_mode else "score"
            r.out.json({"line": text, key: None, "blank": True, "source": rec.source, "lineno": rec.lineno})
        else:
            r.out.write(text)
        return
    column: str
    prob: float | None
    payload: dict[str, Any]
    if answer is None:
        column, prob = "-", None
        payload = {"line": text, "label" if args.label_mode else "score": None, "judged": False}
    elif isinstance(answer, ChoiceAnswer):
        label = answer.choice
        prob = answer.probability
        if args.default and prob < args.threshold:
            label = args.default
        column = label
        payload = {
            "line": text,
            "label": label,
            "probability": round(prob, 4),
            "probabilities": {k: round(v, 4) for k, v in answer.probabilities.items()},
        }
        if r.out.colour:
            column = paint(column, score_colour(prob), True)
    elif isinstance(answer, ScoreAnswer):
        prob = answer.confidence
        column = format_score(answer.normalized, args.range)
        payload = {
            "line": text,
            "score": round(answer.normalized, 4),
            "value": column,
            "level": answer.legend.get(answer.level),
            "confidence": answer.confidence,
        }
        if r.out.colour:
            column = paint(column, score_colour(answer.normalized), True)
    else:  # pragma: no cover - a noul answer cannot come back for these questions
        column, prob, payload = "-", None, {"line": text}
    if args.json:
        payload.update(source=rec.source, lineno=rec.lineno)
        r.out.json(payload)
        return
    if args.with_prob:
        column = f"{column}{args.sep}{fmt_p(prob, 2)}"
    r.out.write(f"{text}{args.sep}{column}" if args.suffix else f"{column}{args.sep}{text}")


async def run(r: Run) -> int:
    args = r.args
    q = question(args)
    stats = {"lines": 0, "unjudged": 0}
    pipe: Pipeline[Answer | None] = Pipeline(concurrency=args.concurrency)

    async def judge(rec: Record) -> Answer | None:
        answers = await r.judge(rec.text, {"tag": q})
        return answers["tag"] if answers else None

    def deliver(rec: Record, answer: Answer | None) -> None:
        if not rec.is_blank():
            stats["lines"] += 1
            if answer is None:
                stats["unjudged"] += 1
            trace(r, verdict(args, answer), rec)
        render(r, rec, answer)

    source = iter_records(args.files or None, max_chars=args.max_chars, stop=pipe.stop_event)
    result = await pipe.run(source, judge, deliver, report_input_error(r))
    if result.fatal is not None:
        raise result.fatal
    report_pipeline_errors(r, result)
    if stats["unjudged"]:
        r.note(f"{stats['unjudged']:,} line(s) could not be judged and were tagged '-'")
    if not stats["lines"]:
        return r.empty_input()
    return partial(EXIT_OK, stats["unjudged"])


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
