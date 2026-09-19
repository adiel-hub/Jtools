"""Every command line printed in the documentation must be one the tools accept.

A README that shows a flag which no longer exists is worse than no README: the reader types it,
gets `unrecognized arguments`, and stops trusting the rest of the page. So every `j*` invocation
in every Markdown file is pulled out, split the way a shell would split it, and handed to that
tool's own argparse parser. Nothing runs and nothing is sent; only the arguments are checked.

Shell syntax the extractor cannot read (process substitution, loops, a line ending in a pipe)
is skipped rather than guessed at, and the test asserts that it still found a healthy number of
commands, so a broken extractor cannot quietly pass by finding nothing.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import re
import shlex
from pathlib import Path

import pytest

from jevtools import TOOLS

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = {*TOOLS, "jtools"}
SPLITTERS = {"|", "||", "&&", ";", "&"}
# A prompt, a comment marker, or a continuation the extractor would have to join up.
PROMPT = re.compile(r"^\s*(?:\$|>|#)\s*")
FENCE = re.compile(r"^```")
# Things whose meaning depends on a running shell; the arguments are not literal.
UNREADABLE = ("$(", "`", "<(", ">(", "${")
# A synopsis (`jtag [options] (--labels CSV | --score DESCRIPTION)`) describes a command's shape;
# it is not one to run, and argparse would reject the brackets.
SYNOPSIS = re.compile(r"[\[\]()]|\.\.\.")


def markdown_files() -> list[Path]:
    skip = {".venv", "node_modules", "dist", "out"}
    return [p for p in sorted(ROOT.rglob("*.md")) if not skip & set(p.parts)]


def code_lines(text: str) -> list[tuple[str, bool]]:
    """(line, is_inline) for every fenced line and every single-backtick span, prompts removed.

    A fenced line is a command somebody could run. An inline span is usually prose naming a tool
    and a flag, as in "the ``--exec`` command in ``jwatch``", so it is held to a stricter rule.
    """
    out: list[tuple[str, bool]] = []
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            out.append((PROMPT.sub("", line), False))
        else:
            out += [(span.replace("\\|", "|"), True) for span in re.findall(r"`([^`\n]+)`", line)]
    return out


def invocations(line: str, inline: bool = False) -> list[list[str]]:
    """The `j*` commands in one shell line, as argv lists."""
    line = line.strip()
    if not line or line.endswith(("\\", "|", "&&")) or any(bad in line for bad in UNREADABLE):
        return []
    try:
        tokens = shlex.split(line, comments=True)
    except ValueError:
        return []  # an unbalanced quote: a fragment, not a command
    found, current = [], []
    for token in [*tokens, ";"]:
        if token in SPLITTERS:
            if keep(current, inline):
                found.append(current)
            current = []
        elif token.startswith((">", "<", "2>")):
            break  # a redirection, and everything after it is a file name
        else:
            current.append(token)
    return found


def recorded_demos() -> list[tuple[str, str]]:
    """(scene file, command) for every recorded demo.

    These are the commands the GIFs in the README were made from. A flag that changes under them
    means the recordings show something the tool no longer accepts.
    """
    import json

    scenes = []
    for scene in sorted((ROOT / "docs" / "demo").glob("*.json")):
        command = json.loads(scene.read_text()).get("command", "")
        if command:
            scenes.append((scene.relative_to(ROOT).as_posix(), command))
    return scenes


def keep(argv: list[str], inline: bool) -> bool:
    """Is this a command line, or prose that happens to name a tool?"""
    if len(argv) < 2 or argv[0] not in COMMANDS or any(SYNOPSIS.search(t) for t in argv):
        return False  # one bare word, or a usage synopsis full of brackets
    # Inside a sentence, "`jwatch --exec`" names a flag; it is not something anyone would run.
    # A real inline example carries something that is not a flag: a description, a file, a
    # subcommand. Inside a fenced block, every line is meant to be run as written.
    return not inline or any(not t.startswith("-") for t in argv[1:])


def documented() -> list[tuple[str, list[str]]]:
    """(where it is written, argv) for every documented invocation, deduplicated."""
    seen: set[tuple[str, ...]] = set()
    out: list[tuple[str, list[str]]] = []
    sources = [(md.relative_to(ROOT).as_posix(), code_lines(md.read_text())) for md in markdown_files()]
    sources += [(where, [(command, False)]) for where, command in recorded_demos()]
    for where, lines in sources:
        for line, inline in lines:
            for argv in invocations(line, inline):
                key = tuple(argv)
                if key not in seen:
                    seen.add(key)
                    out.append((where, argv))
    return out


DOCUMENTED = documented()


def test_the_extractor_finds_the_documented_commands():
    """A guard on the guard: if this drops to nothing, the test below proves nothing."""
    assert len(DOCUMENTED) >= 40, f"only {len(DOCUMENTED)} commands found in the docs; the extractor is broken"
    assert {argv[0] for _, argv in DOCUMENTED} == COMMANDS, "some tool is documented nowhere"
    assert len(recorded_demos()) >= 10, "the recorded demos are not being checked"


@pytest.mark.parametrize("where,argv", DOCUMENTED, ids=[f"{w}: {' '.join(a)[:60]}" for w, a in DOCUMENTED])
def test_a_documented_command_parses(where, argv):
    module = importlib.import_module(f"jevtools.{argv[0]}")
    parser: argparse.ArgumentParser = module.parser()
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            # Each tool's own entry point: jevcore.cli.execute uses parse_intermixed_args, because
            # with a repeatable positional plain parse_args rejects a flag written after the first
            # file name. jtools has subcommands, which parse_intermixed_args cannot handle, and
            # parses with parse_args. Checking with the wrong one would reject valid command lines.
            if argv[0] == "jtools":
                parser.parse_args(argv[1:])
            else:
                parser.parse_intermixed_args(argv[1:])
    except SystemExit as exit_:
        # 0 is --help or --version, which is a legitimate thing to document.
        if exit_.code:
            pytest.fail(f"{where} shows `{' '.join(argv)}`\nbut {argv[0]} rejects it: {err.getvalue().strip()}")
