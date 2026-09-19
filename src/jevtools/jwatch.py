"""jwatch: alert only on lines worth attention in a live stream.  [primitive: noul, streaming]

    tail -f app.log | jwatch "something a human should look at right now"
    journalctl -f | jwatch "a service is failing repeatedly" --exec 'notify-send {}'
    kubectl logs -f deploy/api | jwatch "a customer-visible error" --cooldown 300 --bell

Built for ``tail -f``: every line is judged as it arrives (concurrently, printed in order) and
only the ones that fit are printed. ``--exec`` runs a shell command per alert with ``{}``
replaced by the shell-quoted line; ``--cooldown`` collapses bursts into one alert.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import IO

import httpx

from jevcore import rubric
from jevcore.cli import EXIT_NOMATCH, EXIT_OK, Parser, Run, build_parser, cli_entry, dry_run, execute, partial
from jevcore.errors import UsageError
from jevcore.inputs import Record, iter_records
from jevcore.pipeline import Pipeline
from jevcore.questions import Noul, NoulAnswer

from ._shared import report_input_error, report_pipeline_errors, split_description

PROG = "jwatch"
EXEC_GRACE_SECONDS = 10.0


@dataclass
class _WatchStats:
    alerts: int = 0
    suppressed: int = 0
    unjudged: int = 0
    last_alert: float = float("-inf")


def parser() -> Parser:
    ap = build_parser(
        PROG,
        "Print only the lines of a stream that fit a description; optionally run a command for each.",
        [
            'tail -f app.log | jwatch "something a human should look at right now"',
            "journalctl -f | jwatch \"a service is failing repeatedly\" --exec 'notify-send {}'",
            'kubectl logs -f deploy/api | jwatch "a customer-visible error" --cooldown 300 --bell',
        ],
        usage="jwatch [options] DESCRIPTION [FILE ...]",
    )
    ap.add_argument(
        "--exec",
        metavar="CMD",
        dest="command",
        help="shell command to run per alert; {} is replaced by the quoted line",
    )
    ap.add_argument(
        "--cooldown",
        type=float,
        default=0.0,
        metavar="SEC",
        help="after an alert, swallow further alerts for SEC seconds (reported when it lifts)",
    )
    ap.add_argument("--max", type=int, metavar="N", dest="max_alerts", help="stop after N alerts")
    ap.add_argument("-s", "--with-score", action="store_true", help="prefix each alert with its probability")
    ap.add_argument("--bell", action="store_true", help="ring the terminal bell on each alert")
    ap.add_argument(
        "--all-lines",
        action="store_true",
        help="also print non-matching lines (dimmed on a terminal); a marker shows the alerts",
    )
    return ap


def prepare(args: argparse.Namespace) -> None:
    args.description, args.files = split_description(args.files)
    if args.cooldown < 0:
        raise UsageError("--cooldown must be 0 or more")
    if args.max_alerts is not None and args.max_alerts < 1:
        raise UsageError("--max takes 1 or more")
    if args.command is not None and "{}" not in args.command:
        args.command = args.command + " {}"


def question(args: argparse.Namespace) -> Noul:
    return rubric.fits(args.description)


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    sample = (r.text for r in iter_records(args.files or None, keep_blank=False) if hasattr(r, "text"))
    return dry_run(
        PROG,
        args,
        {"fits": question(args)},
        sample,
        out,
        note="one call per line as it arrives; alerts print in input order",
    )


async def run(r: Run) -> int:
    args = r.args
    q = question(args)
    pipe: Pipeline[float | None] = Pipeline(concurrency=args.concurrency)
    stats = _WatchStats()
    children: set[asyncio.Task[None]] = set()
    loop = asyncio.get_running_loop()

    async def judge(rec: Record) -> float | None:
        answers = await r.judge(rec.text, {"fits": q})
        if not answers:
            return None
        a = answers["fits"]
        return a.probability if isinstance(a, NoulAnswer) else None

    async def spawn(line: str) -> None:
        assert args.command is not None
        cmd = args.command.replace("{}", shlex.quote(line))
        proc = await asyncio.create_subprocess_shell(cmd)
        code = await proc.wait()
        if code:
            r.verbose(f"--exec exited {code}: {cmd[:120]}")

    def alert(rec: Record, p: float) -> None:
        now = time.monotonic()
        if args.cooldown and now - stats.last_alert < args.cooldown:
            stats.suppressed += 1
            r.verbose(f"suppressed by cooldown: {rec.text[:80]}")
            return
        stats.last_alert = now
        stats.alerts += 1
        suffix = ""
        if stats.suppressed:
            suffix = f"  (+{stats.suppressed} more during cooldown)"
            stats.suppressed = 0
        bell = "\a" if args.bell else ""
        if args.json:
            r.out.json(
                {
                    "line": rec.shown,
                    "probability": round(p, 4),
                    "source": rec.source,
                    "lineno": rec.lineno,
                    "alert": True,
                }
            )
        elif args.with_score:
            r.out.scored(p, bell + rec.shown + suffix)
        else:
            r.out.write(bell + rec.shown + suffix)
        if args.command:
            task = loop.create_task(spawn(rec.shown))
            children.add(task)
            task.add_done_callback(children.discard)
        if args.max_alerts and stats.alerts >= args.max_alerts:
            pipe.halt()

    def deliver(rec: Record, p: float | None) -> None:
        if rec.is_blank():
            return
        if p is None:
            stats.unjudged += 1
            if args.all_lines:
                r.out.write(f"?  {rec.shown}")
            return
        if p >= args.threshold:
            alert(rec, p)
        elif args.all_lines:
            r.out.write(f"   {rec.shown}")

    source = iter_records(args.files or None, max_chars=args.max_chars, stop=pipe.stop_event)
    result = await pipe.run(source, judge, deliver, report_input_error(r))
    if result.fatal is not None:
        raise result.fatal
    report_pipeline_errors(r, result)
    if children:
        _done, pending = await asyncio.wait(children, timeout=EXEC_GRACE_SECONDS)
        for t in pending:
            t.cancel()
    if stats.suppressed:
        r.warn(f"{stats.suppressed} alert(s) were suppressed by the cooldown when the input ended")
    if stats.unjudged:
        r.warn(f"{stats.unjudged:,} line(s) could not be judged")
    return partial(EXIT_OK if stats.alerts else EXIT_NOMATCH, stats.unjudged)


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
