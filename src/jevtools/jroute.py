"""jroute: split a stream into buckets.  [primitive: choice]

    cat inbox.txt | jroute "sales:a sales lead" "support:a support request" "spam:junk" --out-dir ./sorted
    tail -f events.log | jroute "alert:needs a human" "noise:routine" --stdout alert | notify

jtag labels lines in place; jroute physically separates them. Each line is classified once and
appended to ``OUT_DIR/<bucket>.txt`` (files are created on first use). Lines whose best bucket
is below the threshold go to ``--default``; lines that could not be judged go there too, or to
``unrouted.txt``, so nothing is ever lost.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import IO

import httpx

from jevcore import rubric
from jevcore.cli import DEFAULT_THRESHOLD, EXIT_OK, Parser, Run, build_parser, cli_entry, dry_run, execute, partial
from jevcore.errors import UsageError
from jevcore.inputs import Record, iter_records
from jevcore.pipeline import Pipeline
from jevcore.questions import Choice, ChoiceAnswer

from ._shared import report_input_error, report_pipeline_errors

PROG = "jroute"
UNROUTED = "unrouted"


def parser() -> Parser:
    ap = build_parser(
        PROG,
        "Partition stdin into one file per bucket by judging which bucket each line belongs to.",
        [
            'cat inbox.txt | jroute "sales:a sales lead" "support:a support request" "spam:junk" --out-dir sorted',
            'tail -f events.log | jroute "alert:needs a human now" "noise:routine chatter" --stdout alert',
            'jroute "en:written in English" "he:written in Hebrew" -i corpus.txt --default other',
        ],
        usage='jroute [options] "NAME:DESCRIPTION" "NAME:DESCRIPTION" ... [-i FILE]',
        files=False,
    )
    ap.add_argument("buckets", nargs="+", metavar="NAME:DESCRIPTION", help="a bucket and what belongs in it")
    ap.add_argument(
        "-i",
        "--input",
        action="append",
        default=[],
        metavar="FILE",
        help="read this file instead of stdin (repeatable)",
    )
    ap.add_argument("-o", "--out-dir", default=".", metavar="DIR", help="where bucket files go (default: .)")
    ap.add_argument("--ext", default=".txt", metavar="EXT", help="bucket file extension (default .txt)")
    ap.add_argument(
        "--default",
        metavar="NAME",
        dest="default_bucket",
        help=f"bucket for lines below -p or unjudged (default: {UNROUTED} for unjudged only)",
    )
    ap.add_argument("--stdout", metavar="NAME", help="also copy this bucket's lines to standard output")
    ap.add_argument("--truncate", action="store_true", help="start bucket files empty instead of appending")
    ap.add_argument("--no-files", action="store_true", help="write no files (use with --json or --stdout)")
    return ap


def prepare(args: argparse.Namespace) -> None:
    args.bucket_map = rubric.parse_buckets(args.buckets)
    if args.default_bucket and args.default_bucket in args.bucket_map:
        raise UsageError("--default must not be one of the judged buckets; use a separate name")
    if args.default_bucket and not re.fullmatch(rubric.NAME, args.default_bucket):
        # A bucket name becomes a file name under --out-dir; keep it a name, not a path.
        raise UsageError("--default may only use letters, digits, dot, dash and underscore")
    if args.ext and ("/" in args.ext or args.ext.strip(".") == ""):
        raise UsageError("--ext must be a plain file extension, for example .txt")
    if args.stdout and args.stdout not in args.bucket_map and args.stdout != args.default_bucket:
        raise UsageError(f"--stdout {args.stdout!r} is not a bucket")
    if args.threshold != DEFAULT_THRESHOLD and not args.default_bucket:
        # Without a bucket to put them in, a low-confidence line has nowhere to go but its best
        # bucket, so -p would silently do nothing at all.
        raise UsageError("-p needs --default NAME: a line below the threshold has to go somewhere")
    if args.ext and not args.ext.startswith("."):
        args.ext = "." + args.ext
    args.files = args.input


def question(args: argparse.Namespace) -> Choice:
    return rubric.classify(args.bucket_map, "Which bucket does the text belong in?")


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    sample = (r.text for r in iter_records(args.files or None, keep_blank=False) if hasattr(r, "text"))
    targets = ", ".join(str(Path(args.out_dir) / f"{b}{args.ext}") for b in args.bucket_map)
    return dry_run(PROG, args, {"bucket": question(args)}, sample, out, note=f"one call per line; files: {targets}")


class Buckets:
    """Lazily opened, line-buffered bucket files."""

    def __init__(self, directory: str, ext: str, truncate: bool, enabled: bool) -> None:
        self.dir = Path(directory)
        self.ext = ext
        self.mode = "w" if truncate else "a"
        self.enabled = enabled
        self.files: dict[str, IO[str]] = {}
        self.counts: dict[str, int] = {}
        self._to_empty: list[str] = []

    def prepare(self, names: Iterable[str]) -> None:
        """Remember which files ``--truncate`` will empty, and empty them at the first write.

        Every bucket this run could use is emptied, not only the ones it fills, or a rerun whose
        verdicts moved would leave yesterday's lines in a file nobody wrote to today. It happens
        at the first write rather than up front, because a run that reads nothing (an empty pipe,
        an upstream failure) must not destroy the previous run's output.
        """
        self._to_empty = list(names) if (self.enabled and self.mode == "w") else []

    def _empty_the_rest(self) -> None:
        if not self._to_empty:
            return
        names, self._to_empty = self._to_empty, []
        self.dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            path = self.dir / f"{name}{self.ext}"
            if path.exists():
                path.write_text("", encoding="utf-8")

    def write(self, bucket: str, line: str) -> None:
        self.counts[bucket] = self.counts.get(bucket, 0) + 1
        if not self.enabled:
            return
        self._empty_the_rest()
        f = self.files.get(bucket)
        if f is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            f = open(self.dir / f"{bucket}{self.ext}", self.mode, encoding="utf-8", buffering=1)  # noqa: SIM115
            self.files[bucket] = f
        f.write(line + "\n")

    def close(self) -> None:
        for f in self.files.values():
            with contextlib.suppress(OSError):
                f.close()


async def run(r: Run) -> int:
    args = r.args
    q = question(args)
    if not args.no_files:
        try:
            Path(args.out_dir).mkdir(parents=True, exist_ok=True)
            probe = Path(args.out_dir) / f".jroute-write-test-{os.getpid()}"
            probe.write_text("")
            probe.unlink()
        except OSError as e:
            raise UsageError(f"cannot write to --out-dir {args.out_dir!r}: {e.strerror or e}") from None
    buckets = Buckets(args.out_dir, args.ext, args.truncate, not args.no_files)
    buckets.prepare([*args.bucket_map, args.default_bucket or UNROUTED, UNROUTED])
    stats = {"lines": 0, "unjudged": 0}
    pipe: Pipeline[ChoiceAnswer | None] = Pipeline(concurrency=args.concurrency)

    async def judge(rec: Record) -> ChoiceAnswer | None:
        answers = await r.judge(rec.text, {"bucket": q})
        a = answers["bucket"] if answers else None
        return a if isinstance(a, ChoiceAnswer) else None

    def deliver(rec: Record, answer: ChoiceAnswer | None) -> None:
        if rec.is_blank():
            return
        stats["lines"] += 1
        prob: float | None
        if answer is None:
            stats["unjudged"] += 1
            bucket, prob = args.default_bucket or UNROUTED, None
        else:
            bucket, prob = answer.choice, answer.probability
            if prob < args.threshold and args.default_bucket:
                bucket = args.default_bucket
        buckets.write(bucket, rec.shown)
        r.verbose(f"{bucket:<12} p={prob if prob is None else f'{prob:.2f}'} {rec.text[:70]}")
        if args.stdout and bucket != args.stdout:
            return  # --stdout selects what reaches stdout, in --json as much as in plain output
        if args.json:
            r.out.json(
                {
                    "bucket": bucket,
                    "line": rec.shown,
                    "probability": None if prob is None else round(prob, 4),
                    "probabilities": {k: round(v, 4) for k, v in answer.probabilities.items()} if answer else None,
                    "source": rec.source,
                    "lineno": rec.lineno,
                }
            )
        elif args.stdout:
            r.out.write(rec.shown)

    try:
        source = iter_records(args.files or None, max_chars=args.max_chars, stop=pipe.stop_event)
        result = await pipe.run(source, judge, deliver, report_input_error(r))
    finally:
        buckets.close()
    if result.fatal is not None:
        raise result.fatal
    report_pipeline_errors(r, result)
    if not stats["lines"]:
        return r.empty_input()
    summary = ", ".join(f"{name}: {n:,}" for name, n in sorted(buckets.counts.items(), key=lambda kv: -kv[1]))
    where = "" if args.no_files else f" -> {os.path.abspath(args.out_dir)}"
    r.note(f"{stats['lines']:,} lines routed ({summary}){where}")
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
