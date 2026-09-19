"""jgrep: grep, but the pattern is a description.  [primitive: noul]

    tail -f app.log | jgrep "a user is getting frustrated"
    jgrep -o "announces a new AI model" titles.txt | sort -rn | head -3
    jgrep -c -p 0.8 "asks for a refund" tickets/*.txt
    jgrep -r --glob '*.md' "a TODO that is really a bug" docs/

Each line becomes one yes/no question. Lines are read as they arrive, judged concurrently and
printed in input order, so it works on ``tail -f`` as well as on files. Exit status follows grep:
0 if anything matched, 1 if nothing did, 2 on a usage or file error (5 when some lines could not
be judged and passed through unjudged).

Several descriptions (``-e``) go in ONE call per line; a line matches if any fits, or all with
``--all``. ``-A``/``-B``/``-C`` print the lines around a match, as in grep; ``--judge-context N``
is the different thing of showing Jev the N lines either side when it decides. ``--para``,
``--whole``, ``--jsonl`` and ``--csv`` change what a record is: a structured record is judged
entire (Jev reads the object) unless ``--field`` names one value inside it.
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
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
from jevcore.inputs import STDIN, InputError, Item, Record, discover, iter_records
from jevcore.io import fmt_p, paint, score_colour
from jevcore.pipeline import Pipeline
from jevcore.questions import Noul, NoulAnswer, State

from ._shared import report_pipeline_errors

PROG = "jgrep"
MAX_ERRORS_SHOWN = 10


@dataclass
class _GroupState:
    matched: int = 0
    stop_all: bool = False


@dataclass
class _Context:
    """The book-keeping -A/-B/-C need: what to hold back, what to still print, what was printed.

    Records arrive here in input order (context and --unordered are refused together), which is
    what lets a match reach back for the lines before it and forward for the lines after.
    """

    before: deque[tuple[Record, str]] = field(default_factory=deque)
    after_left: int = 0
    last_printed: int = 0
    """Line number of the last record printed for this input; 0 before anything."""
    anything_printed: bool = False
    input_id: int | None = None


def parser() -> Parser:
    ap = build_parser(
        PROG,
        "Print lines that fit a plain-English description, as judged by TypeSafe's Jev model.",
        [
            'tail -f app.log | jgrep "a user is getting frustrated"',
            'jgrep -o "about heat or hot water" complaints.txt | sort -rn | head',
            'jgrep -v -p 0.2 "spam" inbox.txt',
            'jgrep -e "about economics" -e "about New York" --all articles.txt',
            'jgrep --jsonl --field message "a payment failed" events.jsonl',
            'jgrep --jsonl "a production deploy outside working hours" deploys.jsonl',
            'jgrep -r --glob "*.py" "reads an environment variable" src/',
        ],
        usage="jgrep [options] DESCRIPTION [FILE ...]",
        short_verbose=False,  # -v is grep's invert-match
        quiet_help="as in grep: print nothing and stop at the first match; the exit status is the answer",
    )
    ap.add_argument(
        "-e",
        dest="descriptions",
        action="append",
        metavar="DESCRIPTION",
        help="a description; repeat for several, judged in one call (a line matches if any fits)",
    )
    ap.add_argument("--all", action="store_true", help="with several -e, a line must fit all of them")
    ap.add_argument("-v", "--invert-match", action="store_true", help="print lines that do NOT match")
    ap.add_argument("-o", "--prob", action="store_true", help="put the probability in a first, tab-separated column")
    ap.add_argument("-n", "--line-number", action="store_true", help="prefix each line with its line number")
    ap.add_argument("-H", "--with-filename", action="store_true", help="prefix each line with its file name")
    ap.add_argument("--no-filename", action="store_true", help="never print file names")
    ap.add_argument("-c", "--count", action="store_true", help="print only a count of matching lines per file")
    ap.add_argument(
        "-l",
        "--files-with-matches",
        action="store_true",
        help="print each file name at its first match and move on to the next file",
    )
    ap.add_argument(
        "-L",
        "--files-without-match",
        action="store_true",
        help="print the name of each file with NO matching record",
    )
    ap.add_argument("-m", "--max-count", type=int, metavar="NUM", help="stop each input after NUM matches")
    ap.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="search directories recursively (skips VCS, dependency and hidden entries, and binaries)",
    )
    ap.add_argument(
        "--glob", action="append", default=[], metavar="PATTERN", help="include only matching files (repeatable)"
    )
    ap.add_argument(
        "--exclude", action="append", default=[], metavar="PATTERN", help="exclude matching paths (repeatable)"
    )
    ap.add_argument("--hidden", action="store_true", help="with -r, also search hidden files and directories")
    unit = ap.add_mutually_exclusive_group()
    unit.add_argument("--para", action="store_true", help="judge paragraphs (blank-line separated), not lines")
    unit.add_argument("--whole", action="store_true", help="judge each file as a whole; print matching file names")
    unit.add_argument("--jsonl", action="store_true", help="read JSON objects; judge the whole record, or just --field")
    unit.add_argument("--csv", action="store_true", help="read CSV with a header; judge the whole row, or just --field")
    ap.add_argument("--field", metavar="NAME", help="judge only this JSON field (dotted path) or CSV column")
    ap.add_argument(
        "-A", "--after-context", type=int, default=0, metavar="N", help="also print N records after each match"
    )
    ap.add_argument(
        "-B", "--before-context", type=int, default=0, metavar="N", help="also print N records before each match"
    )
    ap.add_argument(
        "-C", "--context", type=int, default=0, metavar="N", help="also print N records either side of each match"
    )
    ap.add_argument(
        "--judge-context",
        type=int,
        default=0,
        metavar="N",
        help="show Jev the N records either side when deciding; still one decision per record",
    )
    ap.add_argument("--unordered", action="store_true", help="print matches as answers arrive, not in input order")
    return ap


def prepare(args: argparse.Namespace) -> None:
    positionals = list(args.files)
    if args.descriptions:
        args.files = positionals
    else:
        if not positionals:
            raise UsageError("missing DESCRIPTION")
        args.descriptions, args.files = positionals[:1], positionals[1:]
    if args.max_count is not None and args.max_count < 0:
        raise UsageError("-m takes 0 or more matches")
    if min(args.context, args.after_context, args.before_context, args.judge_context) < 0:
        raise UsageError("-A, -B, -C and --judge-context take 0 or more records")
    if args.field and not (args.jsonl or args.csv):
        raise UsageError("--field requires --jsonl or --csv")
    args.after_context = max(args.after_context, args.context)
    args.before_context = max(args.before_context, args.context)
    if args.whole and (args.after_context or args.before_context or args.judge_context):
        raise UsageError("--whole has nothing around it; -A, -B, -C and --judge-context need records")
    if (args.after_context or args.before_context) and args.unordered:
        # A record's neighbours are only knowable in input order, which is exactly what --unordered
        # gives up. Silently printing the wrong lines around a match would be worse than refusing.
        raise UsageError("-A, -B and -C need input order; --unordered gives it up")
    if (args.after_context or args.before_context) and args.json:
        raise UsageError("-A, -B and -C print surrounding records, which --json has no shape for")
    if args.files_with_matches and args.count:
        raise UsageError("-l and -c cannot be combined")
    if args.files_without_match and args.count:
        raise UsageError("-L and -c cannot be combined")
    if args.files_without_match and args.files_with_matches:
        raise UsageError("-l and -L are opposites; pick one")
    args.mode = "whole" if args.whole else "para" if args.para else "lines"
    args.structured = "jsonl" if args.jsonl else "csv" if args.csv else None


def questions(args: argparse.Namespace) -> dict[str, Noul]:
    build = rubric.fits_in_context if args.judge_context else rubric.fits
    return {f"d{i}": build(d) for i, d in enumerate(args.descriptions)}


def dry(args: argparse.Namespace, out: IO[str]) -> int:
    files, errors = discover(
        args.files, recursive=args.recursive, globs=args.glob, excludes=args.exclude, hidden=args.hidden
    )
    if errors:
        raise UsageError(errors[0])
    sample = (
        state(r, args)
        for r in contextual(
            iter_records(
                files or None,
                mode=args.mode,
                keep_blank=False,
                structured=args.structured,
                field=args.field,
            ),
            args.judge_context,
        )
        if isinstance(r, Record)
    )
    note = "one call per record, all descriptions together" + (
        f"; each record is sent with {args.judge_context} neighbours either side" if args.judge_context else ""
    )
    return dry_run(PROG, args, questions(args), sample, out, note=note)


def contextual(stream: Iterable[Item], n: int) -> Iterator[Item]:
    """Hand each record the n records either side of it, from its own input.

    A record is held back until the n after it have arrived, so on ``tail -f`` a match prints once
    n more lines have come in.
    """
    if n <= 0:
        yield from stream
        return
    before: deque[Record] = deque(maxlen=n)
    waiting: list[tuple[Record, list[Record], list[Record]]] = []
    current: int | None = None

    def ready(force: bool) -> Iterator[Record]:
        while waiting and (force or len(waiting[0][2]) >= n):
            rec, above, below = waiting.pop(0)
            yield replace(rec, before=tuple(r.text for r in above), after=tuple(r.text for r in below))

    for item in stream:
        if isinstance(item, InputError):
            yield item
            continue
        if item.input_id != current:
            yield from ready(True)
            before.clear()
            current = item.input_id
        for _, _, below in waiting:
            if len(below) < n:
                below.append(item)
        waiting.append((item, list(before), []))
        before.append(item)
        yield from ready(False)
    yield from ready(True)


def marked(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.split("\n"))


def state(rec: Record, args: argparse.Namespace) -> State:
    """What Jev is shown: the record alone, or marked with ``>`` inside its context."""
    if not args.judge_context:
        return rec.state
    window = [marked(t, "  ") for t in rec.before]
    window.append(marked(rec.text, "> "))
    window += [marked(t, "  ") for t in rec.after]
    return "\n".join(window)


def render(
    rec: Record, p: float | None, ps: list[float], args: argparse.Namespace, show_file: bool, colour: bool
) -> str:
    """One output record. ``p`` is ``None`` for a record that could not be judged (fail-open)."""
    if args.json:
        obj: dict[str, object] = {"file": rec.source, "line": rec.lineno, "p": None if p is None else round(p, 4)}
        if len(ps) > 1:
            obj["ps"] = [round(x, 4) for x in ps]
        if p is None:
            obj["unjudged"] = True
        if not (args.whole or args.files_with_matches):
            obj["text"] = rec.shown
            if rec.data is not None:
                obj["record"] = rec.data
                if args.field is not None:
                    obj["field"] = args.field
        return json.dumps(obj, ensure_ascii=False)
    if args.whole or args.files_with_matches:
        body = rec.source
    else:
        prefix = (f"{rec.source}:" if show_file else "") + (f"{rec.lineno}:" if args.line_number else "")
        body = prefix + rec.shown
    if args.prob:
        label = fmt_p(p)
        body = f"{paint(label, score_colour(p), colour) if p is not None else label}\t{body}"
    return body + ("\n" if args.para and not (args.whole or args.files_with_matches) else "")


def render_context(rec: Record, args: argparse.Namespace, show_file: bool) -> str:
    """A neighbouring record printed for -A/-B/-C.

    Separated from its file and line by ``-`` rather than ``:``, which is how grep distinguishes a
    line it is showing you from a line that matched, and with no probability column: this record
    was judged, but its verdict is not the reason it is on screen.
    """
    prefix = (f"{rec.source}-" if show_file else "") + (f"{rec.lineno}-" if args.line_number else "")
    body = prefix + rec.shown
    if args.prob:
        body = f"{'-':>5}\t{body}"
    return body + ("\n" if args.para else "")


async def scan(
    r: Run, files: list[str], show_file: bool, totals: dict[str, int], counts: dict[int, int], offset: int = 0
) -> bool:
    """Judge one group of inputs. Returns True when the whole run should stop (quiet match, broken pipe).

    ``counts`` is keyed on the position of the file among the arguments, not on its name, so
    ``jgrep -c pattern a.txt a.txt`` counts each occurrence separately, as grep does.
    """
    args = r.args
    qs = questions(args)
    pipe: Pipeline[tuple[float, list[float]] | None] = Pipeline(
        concurrency=args.concurrency, ordered=not args.unordered
    )
    matched_here = _GroupState()
    headers_written: set[int] = set()

    async def judge(rec: Record) -> tuple[float, list[float]] | None:
        answers = await r.judge(state(rec, args), qs)
        if not answers:
            return None
        ps = [a.probability if isinstance(a, NoulAnswer) else 0.0 for a in (answers[q] for q in qs)]
        return (min(ps) if args.all else max(ps)), ps

    ctx = _Context(before=deque(maxlen=args.before_context or 1))
    showing_context = bool(args.after_context or args.before_context)

    def emit(rec: Record, name: str, p: float | None, ps: list[float]) -> None:
        first_row = rec.header is not None and rec.input_id not in headers_written
        # -o, -n and a file-name prefix all put something before the row, so the columns stop
        # lining up with the header: the output is a view then, not a file, and gets none.
        plain = not (args.prob or args.line_number or show_file)
        if first_row and plain and not (args.json or args.files_with_matches or args.files_without_match):
            r.out.write(rec.header or "")
            headers_written.add(rec.input_id)
        r.out.write(render(replace(rec, source=name), p, ps, args, show_file, r.out.colour))
        ctx.last_printed, ctx.anything_printed = rec.lineno, True

    def emit_context(rec: Record, name: str) -> None:
        r.out.write(render_context(replace(rec, source=name), args, show_file))
        ctx.last_printed, ctx.anything_printed = rec.lineno, True

    def start_group(first_lineno: int) -> None:
        """grep's ``--`` between runs of output that are not next to each other."""
        if ctx.anything_printed and first_lineno > ctx.last_printed + 1:
            r.out.write("--")

    def context_reset(rec: Record) -> None:
        if ctx.input_id != rec.input_id:
            ctx.input_id, ctx.after_left, ctx.last_printed = rec.input_id, 0, 0
            ctx.before.clear()

    def on_match(rec: Record, name: str, p: float | None, ps: list[float]) -> None:
        """Print a match, and with -A/-B/-C the records around it."""
        if not showing_context:
            emit(rec, name, p, ps)
            return
        held = [(b, n) for b, n in ctx.before if b.lineno > ctx.last_printed]
        start_group(held[0][0].lineno if held else rec.lineno)
        for before_rec, before_name in held:
            emit_context(before_rec, before_name)
        emit(rec, name, p, ps)
        ctx.before.clear()
        ctx.after_left = args.after_context

    def on_other(rec: Record, name: str) -> None:
        """A record that did not match: trailing context, or something to hold for -B."""
        if not showing_context:
            return
        if ctx.after_left > 0:
            emit_context(rec, name)
            ctx.after_left -= 1
        elif args.before_context:
            ctx.before.append((rec, name))

    def deliver(rec: Record, value: tuple[float, list[float]] | None) -> None:
        name = STDIN if rec.source == "-" else rec.source
        where = offset + rec.input_id
        totals["seen"] += 1
        counts.setdefault(where, 0)
        context_reset(rec)
        if rec.is_blank():
            p, ps = 0.0, [0.0] * len(qs)
        elif value is None:
            totals["unjudged"] += 1
            # Fail open: an unjudged record passes through (not with -v) so no data is silently
            # dropped. It is not a match: it is not counted and does not stop -m/-q/-l.
            if not args.invert_match and not (
                args.quiet or args.count or args.files_with_matches or args.files_without_match
            ):
                emit(rec, name, None, [])
            else:
                on_other(rec, name)
            return
        else:
            p, ps = value
        if (p >= args.threshold) == args.invert_match:
            on_other(rec, name)
            return
        totals["matched"] += 1
        matched_here.matched += 1
        counts[where] += 1
        if not (args.quiet or args.count or args.files_without_match):
            on_match(rec, name, p, ps)
        if args.quiet:
            matched_here.stop_all = True
            pipe.halt()
        elif (
            args.files_with_matches
            or args.files_without_match
            or (args.max_count and matched_here.matched >= args.max_count)
        ):
            pipe.halt()

    def on_input_error(e: InputError) -> None:
        totals["input_errors"] += 1
        if totals["input_errors"] <= MAX_ERRORS_SHOWN:
            r.warn(e.message)

    source = contextual(
        iter_records(
            files,
            mode=args.mode,
            structured=args.structured,
            field=args.field,
            max_chars=args.max_chars,
            stop=pipe.stop_event,
        ),
        args.judge_context,
    )
    result = await pipe.run(source, judge, deliver, on_input_error)
    if result.fatal is not None:
        raise result.fatal
    report_pipeline_errors(r, result)
    totals["truncated"] += result.truncated
    return matched_here.stop_all


async def run(r: Run) -> int:
    args = r.args
    files, discovery_errors = discover(
        args.files, recursive=args.recursive, globs=args.glob, excludes=args.exclude, hidden=args.hidden
    )
    for message in discovery_errors[:MAX_ERRORS_SHOWN]:
        r.warn(message)
    if len(discovery_errors) > MAX_ERRORS_SHOWN:
        r.warn(f"and {len(discovery_errors) - MAX_ERRORS_SHOWN} more file errors")
    if not files:
        if discovery_errors:
            return EXIT_USAGE
        files = ["-"]
    per_record = (args.csv or args.jsonl) and not args.count
    show_file = not args.no_filename and (args.with_filename or (not per_record and (len(files) > 1 or args.recursive)))
    totals = {"seen": 0, "matched": 0, "unjudged": 0, "input_errors": 0, "truncated": 0}
    counts: dict[int, int] = {}
    if args.max_count == 0:
        counts = dict.fromkeys(range(len(files)), 0)
    else:
        per_file = (
            args.max_count is not None
            or args.files_with_matches
            or args.files_without_match
            or (args.csv and not args.json)
        )
        groups = [[f] for f in files] if per_file else [files]
        for i, group in enumerate(groups):
            # One group per file means input_id is always 0, so the offset carries the position.
            if await scan(r, group, show_file, totals, counts, i if per_file else 0):
                break
    if args.files_without_match:
        # Every file that finished its scan without a single match. counts is keyed on the file's
        # position, so a path repeated on the command line is answered for each occurrence, the
        # way -c already counts them separately.
        without = [f for i, f in enumerate(files) if not counts.get(i)]
        if not args.quiet:
            for f in without:
                r.out.write(STDIN if f == "-" else f)
        if discovery_errors or totals["input_errors"]:
            return EXIT_USAGE
        return partial(EXIT_OK if without else EXIT_NOMATCH, totals["unjudged"])
    if args.count and not args.quiet:
        for i, f in enumerate(files):
            name = STDIN if f == "-" else f
            r.out.write(f"{name}:{counts.get(i, 0)}" if show_file else str(counts.get(i, 0)))
    if totals["truncated"]:
        r.note(f"truncated {totals['truncated']:,} records to {args.max_chars:,} characters; raise --max-chars")
    if totals["input_errors"] > MAX_ERRORS_SHOWN:
        r.warn(f"and {totals['input_errors'] - MAX_ERRORS_SHOWN:,} more input errors")
    if totals["unjudged"]:
        r.note(f"{totals['unjudged']:,} record(s) could not be judged and passed through")
    if args.quiet and totals["matched"]:
        return EXIT_OK
    if discovery_errors or totals["input_errors"]:
        return EXIT_USAGE
    return partial(EXIT_OK if totals["matched"] else EXIT_NOMATCH, totals["unjudged"])


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
