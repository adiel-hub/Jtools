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
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import IO

import httpx

from jevcore import rubric
from jevcore.cli import (
    EXIT_NOMATCH,
    EXIT_OK,
    EXIT_USAGE,
    Parser,
    Run,
    build_parser,
    cli_entry,
    dry_run,
    execute,
    partial,
)
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
        help="shell command to run per alert; {} becomes the line, passed as an argument, never parsed",
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
    if args.command is not None:
        args.command = prepare_exec(args.command)


def prepare_exec(command: str) -> str:
    """Turn ``{}`` into the shell parameter that carries the line, and refuse a spelling it breaks.

    What jwatch guarantees: the line is handed to ``/bin/sh`` as a positional parameter, so it is
    never parsed as shell syntax. No amount of ``$(…)``, backticks or ``;`` in a log line can run.

    What it cannot guarantee is *where* you put the placeholder, any more than a shell script can
    guarantee what you do with ``$1``. Put it where a command word goes and the line is run; that
    is your command, not jwatch's. What is refused is the narrower set of spellings where the
    substitution itself would misbehave:

    - an empty command, or one that is nothing but the placeholder, where the line would become
      the program by default rather than by choice;
    - a placeholder inside quotes of your own, which expands to ``""$1""``: ``$1`` is no longer
      quoted there, and the line is split on whitespace and matched against the filesystem;
    - an escaped placeholder, ``\\{}``, which would substitute into ``\\"$1"`` and leave the shell
      with an unterminated string;
    - a command that ends inside an open quote, which is not a command at all.

    Writing ``$1`` yourself is the escape hatch for putting the line inside a longer string.
    """
    if not command.strip():
        raise UsageError("--exec needs a command; an empty one would run the watched line itself")
    if "{}" not in command and "$1" not in command:
        command += " {}"
    states = list(quote_state(command))
    if states and states[-1][1] and not _closes(command):
        raise UsageError("--exec: the command ends inside an unclosed quote")
    for i, quoted, escaped in states:
        if command[i : i + 2] != "{}":
            continue
        if escaped:
            raise UsageError("--exec: \\{} would substitute into an unterminated string; write {} or $1")
        if quoted:
            raise UsageError(
                "--exec: {} is already quoted for you, so do not put quotes around it. "
                "To put the line inside a longer string, use $1: --exec 'notify-send \"api: $1\"'"
            )
    if command.strip().split()[0] in ("{}", '"$1"', "$1"):
        raise UsageError("--exec: the command cannot be only the line; put {} where an argument goes")
    return command.replace("{}", '"$1"')


def _closes(command: str) -> bool:
    """Does every quote the command opens get closed?"""
    quote = ""
    escaped = False
    for char in command:
        if escaped:
            escaped = False
        elif quote == "'":
            quote = "" if char == "'" else quote
        elif char == "\\":
            escaped = True
        elif quote == '"':
            quote = "" if char == '"' else quote
        elif char in "\"'":
            quote = char
    return not quote


def quote_state(command: str) -> Iterator[tuple[int, bool, bool]]:
    """``(index, inside a quoted string, escaped by a backslash)``, the way a shell reads it.

    Looking only at the characters either side of ``{}`` cannot tell ``'api: {}'`` from
    ``'api: {} '``, and ignoring backslashes cannot tell ``"\\"{}\\""`` (inside the quotes, and
    dangerous) from ``\\" {}`` (outside them, and fine). A shell decides by tracking the quote it
    opened and the escapes inside it, so this does too.
    """
    quote = ""
    escaped = False
    for i, char in enumerate(command):
        if escaped:
            escaped = False
            yield i, bool(quote), True
            continue
        if quote == "'":  # nothing is special inside single quotes, not even a backslash
            yield i, True, False
            if char == "'":
                quote = ""
            continue
        if char == "\\":  # escapes the next character, in double quotes and outside them alike
            escaped = True
            yield i, bool(quote), False
            continue
        if quote == '"':
            yield i, True, False
            if char == '"':
                quote = ""
            continue
        if char in "\"'":
            quote = char
            yield i, True, False
            continue
        yield i, False, False


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
        """Run the command with the line as an argument, never as part of the command text.

        The line comes from the stream being watched, which is exactly the thing an attacker can
        write to. Substituting it into the command and handing that to a shell means a log line
        like ``error $(rm -rf ~)`` runs as code the moment the user quotes ``{}`` themselves.
        ``{}`` becomes the shell parameter ``"$1"`` and the line is passed as that parameter, so
        the shell never parses it.
        """
        assert args.command is not None
        # A NUL cannot be passed through exec at all, and a binary log will contain them. Losing
        # the byte is better than losing the alert, which is what the raw ValueError did.
        safe = line.replace("\0", "")
        try:
            proc = await asyncio.create_subprocess_exec("/bin/sh", "-c", args.command, PROG, safe)
        except (OSError, ValueError) as e:
            r.warn(f"--exec could not start: {e}")
            return
        code = await proc.wait()
        if code:
            r.verbose(f"--exec exited {code}: {args.command[:120]}")

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
        r.note(f"{stats.suppressed} alert(s) were suppressed by the cooldown when the input ended")
    if stats.unjudged:
        r.note(f"{stats.unjudged:,} line(s) could not be judged")
    if r.input_errors:
        # `jwatch "outage" typo.log || echo "no alerts"` must not report "no alerts" for a typo.
        return EXIT_USAGE
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
