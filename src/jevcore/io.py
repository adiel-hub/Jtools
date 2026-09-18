"""Output conventions every tool inherits: flush-per-line writes, colour only on a terminal,
JSON lines, and a dry-run redaction that never sends anything.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import IO, Any

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"
MAGENTA = "\x1b[35m"


class BrokenOutput(Exception):
    """The reader of our stdout went away (``| head``). Stop quietly."""


def wants_colour(mode: str, stream: IO[str]) -> bool:
    if mode == "always":
        return True
    if mode == "never" or os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty()) and os.environ.get("TERM", "") != "dumb"
    except (AttributeError, ValueError):
        return False


def paint(text: str, colour: str, enabled: bool) -> str:
    return f"{colour}{text}{RESET}" if enabled else text


def score_colour(p: float) -> str:
    if p >= 0.75:
        return GREEN
    if p >= 0.4:
        return YELLOW
    return RED


def fmt_p(p: float | None, digits: int = 3) -> str:
    return "-" if p is None else f"{p:.{digits}f}"


class Output:
    """A text sink that flushes every line (pipes must not buffer) and treats EPIPE as a stop."""

    def __init__(self, stream: IO[str] | None = None, colour: str = "auto") -> None:
        self.stream = stream or sys.stdout
        self.colour = wants_colour(colour, self.stream)
        self.lines = 0

    def write(self, text: str) -> None:
        try:
            self.stream.write(text + "\n")
            self.stream.flush()
        except BrokenPipeError:
            raise BrokenOutput from None
        except ValueError:
            # Closed stream (a test tore it down); nothing useful left to do.
            raise BrokenOutput from None
        self.lines += 1

    def json(self, obj: dict[str, Any]) -> None:
        self.write(json.dumps(obj, ensure_ascii=False))

    def scored(self, p: float | None, text: str, sep: str = "\t") -> None:
        label = fmt_p(p)
        if self.colour and p is not None:
            label = paint(label, score_colour(p), True)
        self.write(f"{label}{sep}{text}")


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DIGITS = re.compile(r"\d{4,}")
_TOKEN = re.compile(r"\b(?:sk|vck|key|token|bearer)[-_ ]?[A-Za-z0-9_\-]{8,}\b", re.I)


def redact(text: str, limit: int = 120) -> str:
    """A sample safe to print in ``--dry-run``: emails, long digit runs and key-like tokens masked."""
    text = _TOKEN.sub("<token>", text)
    text = _EMAIL.sub("<email>", text)
    text = _DIGITS.sub(lambda m: m.group(0)[:2] + "…", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def eprint(prog: str, message: str, stream: IO[str] | None = None) -> None:
    print(f"{prog}: {message}", file=stream or sys.stderr, flush=True)
