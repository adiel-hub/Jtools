"""The command-line surface every tool shares: flags, exit codes, dry runs, the stats line.

Exit codes, grep-style and then some::

    0  ok / matched          3  auth error (no key, key rejected)
    1  no matches            4  API error or budget exhausted (with --strict, the first one)
    2  usage error           5  partial: some lines could not be judged and passed through unjudged
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import sys
from collections.abc import Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any

import httpx

from . import __version__
from .auth import Credentials, resolve
from .backends import BACKEND_NAMES, config_dir
from .client import DEFAULT_CONCURRENCY, DEFAULT_TIMEOUT, ErrorReporter, Jev
from .errors import AuthError, BudgetExceeded, JevError, JevFatal, UsageError
from .inputs import DEFAULT_MAX_CHARS
from .io import BrokenOutput, Output, eprint, redact
from .questions import Answer, Question, State, canonical

EXIT_OK = 0
EXIT_NOMATCH = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_API = 4
EXIT_PARTIAL = 5
EXIT_INTERRUPTED = 130

DEFAULT_BUDGET = 1.0  # dollars; a per-line billed command needs a seat belt
ENV_BUDGET = "JEV_BUDGET"

KEY_HELP = (
    "Jev is reached through TypeSafe (TYPESAFE_API_KEY), OpenRouter (OPENROUTER_API_KEY), the Vercel AI\n"
    "Gateway (AI_GATEWAY_API_KEY) or a System One gateway of your own (JEV_GATEWAY_URL + JEV_GATEWAY_API_KEY).\n"
    f"Keys can also live in {config_dir()}/typesafe.key, openrouter.key, vercel.key or gateway.key."
)


class Parser(argparse.ArgumentParser):
    """argparse that raises :class:`UsageError` instead of exiting, so tools return 2 cleanly."""

    def error(self, message: str) -> Any:
        raise UsageError(message)


def build_parser(
    prog: str,
    description: str,
    examples: Sequence[str],
    *,
    usage: str | None = None,
    threshold: float | None = 0.5,
    files: bool = True,
    short_verbose: bool = True,
) -> Parser:
    epilog = "examples:\n" + "".join(f"  {e}\n" for e in examples) + "\n" + KEY_HELP
    ap = Parser(
        prog=prog,
        description=description,
        epilog=epilog,
        usage=usage,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    add_common(ap, threshold=threshold, files=files, short_verbose=short_verbose)
    return ap


def add_common(
    ap: argparse.ArgumentParser, *, threshold: float | None = 0.5, files: bool = True, short_verbose: bool = True
) -> None:
    g = ap.add_argument_group("common options (every j-tool)")
    if threshold is not None:
        g.add_argument(
            "-p",
            "--threshold",
            type=float,
            default=threshold,
            metavar="P",
            help=f"probability needed for a positive verdict (default {threshold})",
        )
    g.add_argument("--json", action="store_true", help="one JSON object per output record, with scores")
    g.add_argument(
        "--dry-run",
        action="store_true",
        help="print the backend, the exact questions and a redacted input sample; call nothing",
    )
    g.add_argument("--model", metavar="ID", help="model ID to request (default: the backend's latest Jev)")
    g.add_argument("--api", choices=BACKEND_NAMES, help="which API to call (default: whichever has a key)")
    g.add_argument(
        "-j",
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        metavar="N",
        help=f"requests in flight (default {DEFAULT_CONCURRENCY})",
    )
    g.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"give up on one request after this long, retries and rate-limit waits included "
        f"(default {DEFAULT_TIMEOUT:g}, or $JEV_TIMEOUT)",
    )
    g.add_argument(
        "--budget",
        type=float,
        default=None,
        metavar="DOLLARS",
        help=f"stop once this much is spent (default {DEFAULT_BUDGET:.2f}, or ${ENV_BUDGET}; 0 = no limit)",
    )
    g.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        metavar="N",
        help=f"judge only the first N characters of a record (default {DEFAULT_MAX_CHARS})",
    )
    g.add_argument("--no-cache", action="store_true", help="do not read or write the on-disk answer cache")
    g.add_argument(
        "--strict",
        action="store_true",
        help="fail closed: stop with exit 4 on the first API error instead of passing lines through",
    )
    g.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colour scores (default: only on a terminal)",
    )
    g.add_argument(
        "--stats",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="print calls, tokens, cost and latency to stderr (default: when stderr is a terminal)",
    )
    g.add_argument("-q", "--quiet", action="store_true", help="print less (tool-specific; see --help)")
    verbose_flags = ("-v", "--verbose") if short_verbose else ("--verbose",)
    g.add_argument(*verbose_flags, action="store_true", help="explain decisions on stderr")
    g.add_argument("--version", action="version", version=f"%(prog)s {__version__} (jev-tools)")
    if files:
        ap.add_argument("files", nargs="*", metavar="FILE", help="input files; none or - means standard input")


def validate_common(args: argparse.Namespace) -> None:
    if getattr(args, "threshold", None) is not None and not (
        math.isfinite(args.threshold) and 0.0 <= args.threshold <= 1.0
    ):
        raise UsageError("-p/--threshold must be a probability from 0 to 1")
    if args.concurrency < 1:
        raise UsageError("-j/--concurrency takes 1 or more")
    if not (math.isfinite(args.timeout) and args.timeout > 0):
        raise UsageError("--timeout must be finite and greater than 0")
    if args.max_chars < 1:
        raise UsageError("--max-chars must be greater than 0")
    if args.budget is None:
        raw = os.environ.get(ENV_BUDGET)
        try:
            args.budget = float(raw) if raw else DEFAULT_BUDGET
        except ValueError:
            raise UsageError(f"{ENV_BUDGET} must be a number of dollars; got {raw!r}") from None
    if not (math.isfinite(args.budget) and args.budget >= 0):
        raise UsageError("--budget must be finite and not negative")


@dataclass
class Run:
    """Everything a tool's ``run()`` needs besides its own arguments."""

    prog: str
    args: argparse.Namespace
    jev: Jev
    out: Output
    err: IO[str]
    reporter: ErrorReporter

    @property
    def strict(self) -> bool:
        return bool(self.args.strict)

    async def judge(self, state: State, questions: Mapping[str, Question]) -> dict[str, Answer] | None:
        """Ask, honouring ``--strict``: fail closed (raise) or fail open (``None``)."""
        if self.strict:
            return await self.jev.ask(state, questions)
        return await self.jev.try_ask(state, questions)

    def warn(self, message: str) -> None:
        eprint(self.prog, message, self.err)

    def verbose(self, message: str) -> None:
        if self.args.verbose:
            eprint(self.prog, message, self.err)


def make_jev(
    prog: str,
    args: argparse.Namespace,
    err: IO[str],
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[Jev, ErrorReporter]:
    credentials: Credentials = resolve(args.api)
    reporter = ErrorReporter(err, prefix=prog)
    jev = Jev(
        credentials,
        model=args.model,
        timeout=args.timeout,
        concurrency=args.concurrency,
        budget=args.budget,
        disk_cache=not args.no_cache and not os.environ.get("JEV_NO_CACHE"),
        transport=transport,
        on_error=reporter,
        prefix=prog,
    )
    return jev, reporter


def dry_run(
    prog: str,
    args: argparse.Namespace,
    questions: Mapping[str, Question],
    samples: Iterable[State],
    out: IO[str],
    note: str | None = None,
) -> int:
    """Show what would be sent, send nothing, exit 0."""
    try:
        creds = resolve(args.api)
        backend = f"{creds.backend.name} ({creds.url}), key {creds.redacted_key}"
        model = args.model or os.environ.get("JEV_MODEL") or creds.backend.model
    except AuthError as e:
        backend = f"none usable ({e})"
        model = args.model or "jev-latest"
    print(f"{prog}: dry run; nothing is sent", file=out)
    print(f"backend: {backend}", file=out)
    print(f"model:   {model}", file=out)
    if note:
        print(f"note:    {note}", file=out)
    print("questions:", file=out)
    for qid, q in questions.items():
        print(f"  {qid}: {canonical(q)}", file=out)
    shown = 0
    print("sample state (redacted):", file=out)
    for state in samples:
        text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        print(f"  {redact(text)}", file=out)
        shown += 1
        if shown >= 3:
            break
    if not shown:
        print("  (no input)", file=out)
    out.flush()
    return EXIT_OK


def partial(code: int, unjudged: int) -> int:
    """A run where some lines passed through unjudged reports 5 instead of a clean 0 or 1."""
    return EXIT_PARTIAL if unjudged and code in (EXIT_OK, EXIT_NOMATCH) else code


def stats_wanted(args: argparse.Namespace, err: IO[str]) -> bool:
    if args.stats is not None:
        return bool(args.stats)
    try:
        return bool(err.isatty())
    except (AttributeError, ValueError):
        return False


def execute(
    prog: str,
    parser: Parser,
    argv: Sequence[str] | None,
    prepare: Callable[[argparse.Namespace], None] | None,
    body: Callable[[Run], Coroutine[Any, Any, int]],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
    dry: Callable[[argparse.Namespace, IO[str]], int] | None = None,
) -> int:
    """Parse, validate, build the client, run the tool, print stats, map exceptions to exit codes.

    ``prepare(args)`` does tool-specific validation (raise :class:`UsageError`). ``dry(args, out)``
    handles ``--dry-run``. ``body(run)`` is the tool.
    """
    out_stream = out or sys.stdout
    err_stream = err or sys.stderr
    try:
        args = parser.parse_intermixed_args(argv)
        validate_common(args)
        if prepare is not None:
            prepare(args)
    except UsageError as e:
        eprint(prog, str(e), err_stream)
        print(f"try '{prog} --help'", file=err_stream, flush=True)
        return EXIT_USAGE
    except SystemExit as e:  # --help / --version
        return e.code if isinstance(e.code, int) else EXIT_OK

    if args.dry_run:
        if dry is None:
            eprint(prog, "this tool has no dry run", err_stream)
            return EXIT_USAGE
        try:
            return dry(args, out_stream)
        except UsageError as e:
            eprint(prog, str(e), err_stream)
            return EXIT_USAGE

    try:
        jev, reporter = make_jev(prog, args, err_stream, transport)
    except AuthError as e:
        eprint(prog, str(e), err_stream)
        return EXIT_AUTH

    output = Output(out_stream, args.color)
    run = Run(prog, args, jev, output, err_stream, reporter)

    async def main() -> int:
        try:
            return await body(run)
        finally:
            await jev.close()

    summary_note = ""
    try:
        code = asyncio.run(main())
    except KeyboardInterrupt:
        code, summary_note = EXIT_INTERRUPTED, "interrupted; "
    except AuthError as e:
        eprint(prog, str(e), err_stream)
        code = EXIT_AUTH
    except BudgetExceeded as e:
        eprint(prog, str(e), err_stream)
        code = EXIT_API
    except JevFatal as e:
        eprint(prog, str(e), err_stream)
        code = EXIT_API
    except JevError as e:  # --strict: the first per-request error ends the run
        eprint(prog, str(e), err_stream)
        code = EXIT_API
    except UsageError as e:
        eprint(prog, str(e), err_stream)
        code = EXIT_USAGE
    except BrokenOutput:
        code = EXIT_OK
    reporter.flush()
    if stats_wanted(args, err_stream):
        eprint(prog, summary_note + jev.meter.summary(), err_stream)
    return code


def cli_entry(main: Callable[[], int]) -> None:
    """Console-script wrapper: flush, tolerate a closed pipe, and exit without waiting for a
    reader thread that may still be blocked on stdin (``tail -f``)."""
    code = main()
    try:
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    with contextlib.suppress(BrokenPipeError, ValueError):
        sys.stderr.flush()
    os._exit(code)
