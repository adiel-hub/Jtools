"""jtools: the umbrella command.

jtools list      the ten tools and what each decides
jtools doctor    check the key, make one real call, report latency, tokens and cost
jtools version
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import IO

import httpx

from jevcore import __version__
from jevcore.auth import available, resolve
from jevcore.backends import BACKEND_NAMES, BACKENDS, cache_dir, config_dir
from jevcore.cli import EXIT_API, EXIT_AUTH, EXIT_OK, EXIT_USAGE, Parser, cli_entry, safe_url
from jevcore.client import Jev
from jevcore.errors import AuthError, JevError, UsageError

from . import TOOLS

PROG = "jtools"


def parser() -> Parser:
    ap = Parser(
        prog=PROG,
        description="j-tools: semantic judgment for the shell.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("list", help="list the tools")
    doctor = sub.add_parser("doctor", help="check the key and make one real call")
    doctor.add_argument("--api", choices=BACKEND_NAMES, help="which API to check (default: whichever has a key)")
    doctor.add_argument("--model", metavar="ID")
    sub.add_parser("version", help="print the version")
    ap.add_argument("--version", action="version", version=f"jtools {__version__}")
    return ap


def list_tools(out: IO[str]) -> int:
    width = max(len(name) for name in TOOLS)
    print("j-tools: semantic judgment for the shell\n", file=out)
    for name, blurb in TOOLS.items():
        print(f"  {name:<{width}}  {blurb}", file=out)
    print(f"\nRun any tool with --help. Keys: {', '.join(b.key_env for b in BACKENDS.values())}", file=out)
    return EXIT_OK


async def _doctor(args: argparse.Namespace, out: IO[str], transport: httpx.AsyncBaseTransport | None) -> int:
    print(f"jev-tools {__version__}  python {sys.version.split()[0]}", file=out)
    print(f"config dir: {config_dir()}   cache dir: {cache_dir()}", file=out)
    usable = available()
    for backend in BACKENDS.values():
        state = "key found" if backend in usable else "no key"
        print(f"  {backend.name:<11} {state:<10} {backend.key_env}", file=out)
    try:
        creds = resolve(args.api)
    except AuthError as e:
        print(f"\nno usable backend: {e}", file=out)
        return EXIT_AUTH
    jev = Jev(creds, model=args.model, transport=transport, disk_cache=False)
    # safe_url, not creds.url: a gateway may carry its token in the query string or the userinfo,
    # and doctor output is the first thing anyone pastes into a bug report.
    print(
        f"\nusing {creds.backend.name} at {safe_url(creds.url)} with key {creds.redacted_key}, model {jev.model}",
        file=out,
    )
    try:
        seconds, usage = await jev.ping()
    except AuthError as e:
        print(f"key rejected: {e}", file=out)
        return EXIT_AUTH
    except (JevError, httpx.HTTPError) as e:
        print(f"call failed: {e}", file=out)
        return EXIT_API
    finally:
        await jev.close()
    cost = usage.cost if usage.cost is not None else usage.input_tokens * 0.042 / 1e6
    print(
        f"ok: one call in {seconds * 1000:.0f} ms, {usage.input_tokens} input tokens, ${cost:.7f}"
        + (f", answered by {usage.model}" if usage.model else ""),
        file=out,
    )
    if usage.model and usage.model != jev.model:
        # Worth saying plainly: the cache is keyed on the name you ask for, so this is the version
        # those answers came from, and pinning it with --model makes a rerun free.
        print(f"note: {jev.model} currently means {usage.model}; pin it with --model for exact reruns", file=out)
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    out_stream, err_stream = out or sys.stdout, err or sys.stderr
    try:
        args = parser().parse_args(argv)
    except UsageError as e:
        print(f"{PROG}: {e}", file=err_stream)
        return EXIT_USAGE
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_OK
    if args.command in (None, "list"):
        return list_tools(out_stream)
    if args.command == "version":
        print(f"jtools {__version__}", file=out_stream)
        return EXIT_OK
    return asyncio.run(_doctor(args, out_stream, transport))


def cli() -> None:
    cli_entry(main)


if __name__ == "__main__":
    cli()
