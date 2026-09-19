"""jgate: a conditional pipe barrier.  [primitive: noul]

    git diff | jgate "this change is safe to auto-merge" && git merge
    cat report.txt | jgate "mentions a security incident" && alert-oncall
    tail -50 app.log | jgate --each "a request took longer than a second" && page-me

Judges the whole input as one state (or each line with ``--each``) and speaks only through its
exit status: 0 when the probability reaches the threshold, 1 when it does not. Nothing is
printed unless you ask (``--print`` copies the input through on success, ``--json`` reports the
verdict).

A gate fails **closed**: if Jev cannot be reached the exit status is 4 and the ``&&`` chain stops.
That is the one place j-tools does not fail open by default, because a gate that lets ``git merge``
run when the API is down is not a gate. ``--fail-open`` restores the pass-through behaviour.
"""

from __future__ import annotations

import argparse
import contextlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import IO

import httpx

from jevcore import rubric
from jevcore.cli import (
    EXIT_API,
    EXIT_NOMATCH,
    EXIT_OK,
    EXIT_USAGE,
    Parser,
    Run,
    build_parser,
    cli_entry,
    dry_run,
    execute,
)
from jevcore.errors import JevError, UsageError
from jevcore.inputs import DEFAULT_MAX_CHARS, Item, Record, iter_records
from jevcore.io import BrokenOutput
from jevcore.pipeline import Pipeline
from jevcore.questions import Noul, NoulAnswer

from ._shared import read_all, report_input_error, report_pipeline_errors, split_description

PROG = "jgate"
WHOLE_MAX_CHARS = 60_000


def parser() -> Parser:
    ap = build_parser(
        PROG,
        "Exit 0 if the input fits a description, 1 if not. Prints nothing by default; use it before &&.",
        [
            'git diff | jgate "this change is safe to auto-merge" && git merge',
            'cat report.txt | jgate "mentions a security incident" && alert-oncall',
            'tail -50 app.log | jgate --each "a request took longer than a second" && page-me',
            'cat draft.md | jgate -P "ready to publish" | publish',
        ],
        usage="jgate [options] DESCRIPTION [FILE ...]",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--each", action="store_true", help="judge each line; pass if ANY line fits")
    mode.add_argument("--all", action="store_true", help="judge each line; pass only if ALL non-blank lines fit")
    ap.add_argument(
        "-P",
        "--print",
        action="store_true",
        dest="passthrough",
        help="on success, copy the input to stdout (so the gate can sit inside a pipe)",
    )
    ap.add_argument(
        "--fail-open", action="store_true", help="pass (exit 0) when Jev cannot be reached, instead of exit 4"
    )
    return ap


def prepare(args: argparse.Namespace) -> None:
    args.description, args.files = split_description(args.files)
    if args.fail_open and args.strict:
        raise UsageError("--fail-open and --strict contradict each other")
    args.per_line = args.each or args.all
    if not args.per_line and args.max_chars == DEFAULT_MAX_CHARS:
        # A whole document is the state here; the per-line default would truncate most of it.
        args.max_chars = WHOLE_MAX_CHARS


def question(args: argparse.Namespace) -> Noul:
    return rubric.fits(args.description)


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    if args.per_line:
        sample = (r.text for r in iter_records(args.files or None, keep_blank=False) if hasattr(r, "text"))
        note = "one call per line; " + ("ANY line" if args.each else "ALL lines") + " must reach the threshold"
    else:
        sample = (
            r.text
            for r in iter_records(args.files or None, mode="whole", max_chars=args.max_chars)
            if hasattr(r, "text")
        )
        note = f"the whole input is ONE state (first {args.max_chars:,} characters)"
    return dry_run(PROG, args, {"fits": question(args)}, sample, out, note=note)


def unreadable(r: Run) -> int:
    """A gate that could not read part of its input has not judged that part.

    Every other tool treats this as exit 2. For a gate it matters more: `jgate --all "safe" *.log
    && deploy` must not deploy because one of the files was missing. The message has already been
    printed by the reader; this only decides the status.
    """
    r.warn("some input could not be read, so the verdict would not cover it")
    return EXIT_USAGE


def check_files(r: Run) -> bool:
    """Open every named file before judging anything. True when they can all be read.

    The reader would report an unreadable file on its own, but `--each` stops at the first line
    that settles the verdict, and a file after that one is never opened at all. A gate has to know
    what it is being asked about before it answers, so the arguments are checked up front.
    """
    bad = []
    for path in r.args.files:
        if path == "-":
            continue
        try:
            with open(path, "rb"):
                pass
        except OSError as e:
            bad.append(f"{path}: {e.strerror or e}")
    for message in bad:
        r.warn(message)
    return not bad


def verdict(r: Run, p: float | None, *, extra: dict[str, object] | None = None) -> int:
    args = r.args
    if p is None:
        if args.fail_open:
            r.warn("could not judge the input; --fail-open lets it pass")
            code = EXIT_OK
        else:
            r.warn("could not judge the input; failing closed (use --fail-open to pass instead)")
            code = EXIT_API
    else:
        code = EXIT_OK if p >= args.threshold else EXIT_NOMATCH
    if args.json:
        obj: dict[str, object] = {"pass": code == EXIT_OK, "probability": p, "threshold": args.threshold}
        obj.update(extra or {})
        # A gate keeps its verdict when it loses stdout. Elsewhere a closed pipe means "the reader
        # has what it wanted", exit 0; here it would turn a refusal into a pass under `| head`.
        with contextlib.suppress(BrokenOutput):
            r.out.json(obj)
    elif p is not None:
        r.verbose(f"p={p:.3f} threshold={args.threshold:g} -> {'pass' if code == EXIT_OK else 'fail'}")
    return code


async def gate_whole(r: Run) -> int:
    if not check_files(r):
        return unreadable(r)
    records = await read_all(r, r.args.files, keep_blank=True, mode="whole")
    if r.input_errors:
        return unreadable(r)
    text = "\n".join(rec.text.rstrip("\n") for rec in records)
    if not text.strip():
        return r.empty_input()
    if any(rec.truncated for rec in records):
        r.warn(f"input truncated to {r.args.max_chars:,} characters per file; raise --max-chars")
    try:
        # r.judge, like everywhere else, so --strict is honoured here rather than reaching the
        # same exit code by a different route.
        answers = await r.judge(text, {"fits": question(r.args)})
    except JevError:
        if not r.args.fail_open:
            raise
        r.warn("Jev unreachable; --fail-open lets the input pass")
        answers = None
        code = EXIT_OK
    else:
        p = answers["fits"].probability if answers and isinstance(answers["fits"], NoulAnswer) else None
        code = verdict(r, p)
    if code == EXIT_OK and r.args.passthrough:
        with contextlib.suppress(BrokenOutput):
            for rec in records:
                r.out.write(rec.shown.rstrip("\n"))
    return code


@dataclass
class _EachStats:
    judged: int = 0
    passed: int = 0
    failed_calls: int = 0
    truncated: int = 0
    decided: int | None = None


async def gate_each(r: Run) -> int:
    args = r.args
    if not check_files(r):
        return unreadable(r)
    q = question(args)
    kept: list[Record] = []
    stats = _EachStats()
    pipe: Pipeline[float | None] = Pipeline(concurrency=args.concurrency, ordered=False)

    async def judge(rec: Record) -> float | None:
        answers = await r.judge(rec.text, {"fits": q})  # r.judge, so --strict fails closed here too
        if not answers:
            return None
        a = answers["fits"]
        return a.probability if isinstance(a, NoulAnswer) else None

    def deliver(rec: Record, p: float | None) -> None:
        if rec.is_blank():
            return
        stats.judged += 1
        stats.truncated += int(rec.truncated)
        if p is None:
            stats.failed_calls += 1
            if not args.fail_open and args.all:
                stats.decided = EXIT_API  # one unjudged line means ALL cannot be asserted
                pipe.halt()
            return
        fits = p >= args.threshold
        stats.passed += int(fits)
        r.verbose(f"{rec.lineno}: p={p:.3f} {'fits' if fits else 'no'}: {rec.text[:80]}")
        if args.each and fits and not args.passthrough:
            stats.decided = EXIT_OK
            pipe.halt()
        if args.all and not fits and not args.passthrough:
            stats.decided = EXIT_NOMATCH
            pipe.halt()

    source: Iterable[Item]
    if args.passthrough:
        # -P can only echo after the verdict, so the input is read first; it is then echoed
        # complete and in input order whatever the judging order (or outcome) was.
        kept = await read_all(r, args.files, keep_blank=True)
        source = kept
    else:
        source = iter_records(args.files or None, max_chars=args.max_chars, stop=pipe.stop_event)
    result = await pipe.run(source, judge, deliver, report_input_error(r))
    if result.fatal is not None:
        if isinstance(result.fatal, JevError) and args.fail_open:
            r.warn("Jev unreachable; --fail-open lets the input pass")
            for rec in kept:
                r.out.write(rec.shown)
            return EXIT_OK
        raise result.fatal
    report_pipeline_errors(r, result)
    if stats.truncated:
        r.warn(f"{stats.truncated:,} record(s) were judged on their first {args.max_chars:,} characters")
    if r.input_errors:
        return unreadable(r)
    if stats.judged == 0 and not kept:
        return r.empty_input()
    if stats.decided is not None:
        code = stats.decided
    elif stats.failed_calls and stats.judged == stats.failed_calls:
        code = verdict(r, None)  # nothing could be judged at all
    elif args.all:
        judged_ok = stats.judged - stats.failed_calls
        code = EXIT_OK if stats.passed and stats.passed == judged_ok else EXIT_NOMATCH
    elif stats.passed:
        code = EXIT_OK
    elif stats.failed_calls and not args.fail_open:
        # No judged line fits, but some lines could not be judged: "no" cannot be asserted.
        code = verdict(r, None)
    else:
        code = EXIT_NOMATCH
    with contextlib.suppress(BrokenOutput):  # the verdict survives losing stdout; see verdict()
        if args.json:
            r.out.json(
                {
                    "pass": code == EXIT_OK,
                    "mode": "all" if args.all else "any",
                    "lines": stats.judged,
                    "fit": stats.passed,
                    "unjudged": stats.failed_calls,
                    "threshold": args.threshold,
                }
            )
        if code == EXIT_OK and args.passthrough:
            for rec in kept:
                r.out.write(rec.shown)
    return code


async def run(r: Run) -> int:
    try:
        return await (gate_each(r) if r.args.per_line else gate_whole(r))
    except JevError:
        if r.args.fail_open:
            r.warn("Jev unreachable; --fail-open lets the input pass")
            return EXIT_OK
        raise


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
