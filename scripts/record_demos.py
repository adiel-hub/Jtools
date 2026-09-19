"""Run the real tools on the example inputs and save what happened as terminal "casts".

    uv run python scripts/record_demos.py            # live: a few dozen calls, well under a cent

Each cast is a JSON file in docs/demo/ with the command, the exact stdout/stderr and per-line
timing, plus an asciinema v2 .cast next to it. scripts/render_demo.py turns casts into GIFs and
SVGs for the README. Nothing here is typed by hand: every frame is real output.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
DEMOS = ROOT / "docs" / "demo"

# (name, argv, stdin file or None, title)
SCENES: list[tuple[str, list[str], str | None, str]] = [
    ("jsort", ["jsort", "angriest customer first", "--with-score", "examples/feedback.txt"], None, "sort by meaning"),
    (
        "jroute",
        [
            "jroute",
            "sales:a sales lead",
            "support:a support request",
            "spam:junk or a scam",
            "-i",
            "examples/inbox.txt",
            "-o",
            "/tmp/sorted",
            "--truncate",
            "--json",
        ],
        None,
        "split a stream into buckets",
    ),
    (
        "jwatch",
        ["jwatch", "something an operator should look at right now", "--with-score"],
        "examples/app.log",
        "alert only on what matters",
    ),
    ("juniq", ["juniq", "the same feature request", "-c", "examples/requests.txt"], None, "semantic dedup"),
    ("jtag", ["jtag", "--labels", "bug,feature,question,complaint", "examples/tickets.txt"], None, "label every line"),
    (
        "jmatch",
        [
            "jmatch",
            "examples/invoices.txt",
            "examples/payments.txt",
            "the payment that settles this invoice",
            "--unmatched",
            "--format",
            "{a}  ->  {b}  ({score})",
        ],
        None,
        "semantic join",
    ),
    ("jhead", ["jhead", "3", "a decision that was made", "examples/thread.txt"], None, "the N most relevant lines"),
    ("jpick", ["jpick", "the most urgent thing to fix", "--why", "examples/tickets.txt"], None, "choose one"),
    ("jgrep", ["jgrep", "-o", "a payment problem", "examples/inbox.txt"], None, "grep by meaning"),
    (
        "jgate",
        ["sh", "-c", 'jgate "mentions a security incident" examples/app.log && echo "exit 0 -> page security"'],
        None,
        "a pipe barrier",
    ),
]


def run_scene(name: str, argv: list[str], stdin_file: str | None) -> dict[str, object]:
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "dumb", "NO_COLOR": "1"}
    stdin = open(ROOT / stdin_file, encoding="utf-8") if stdin_file else subprocess.DEVNULL  # noqa: SIM115
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        argv, cwd=ROOT, env=env, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None and proc.stderr is not None
    out_events: list[tuple[float, str]] = []
    for line in proc.stdout:
        out_events.append((round(time.perf_counter() - t0, 3), line.rstrip("\n")))
    err = proc.stderr.read()
    code = proc.wait()
    seconds = round(time.perf_counter() - t0, 3)
    if stdin_file:
        stdin.close()  # type: ignore[union-attr]
    shown = " ".join(shlex.quote(a) if " " in a or '"' in a else a for a in argv)
    if stdin_file:
        shown = f"cat {stdin_file} | {shown}"
    return {
        "name": name,
        "command": shown,
        "stdout": out_events,
        "stderr": err.strip().splitlines(),
        "exit": code,
        "seconds": seconds,
    }


def asciicast(scene: dict[str, object], title: str) -> str:
    header = {
        "version": 2,
        "width": 100,
        "height": 24,
        "timestamp": int(time.time()),
        "title": f"j-tools: {title}",
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
    }
    events: list[list[object]] = []
    t = 0.0
    cmd = str(scene["command"])
    events.append([t, "o", "\u001b[1;32m$\u001b[0m "])
    for ch in cmd:
        t += 0.03
        events.append([t, "o", ch])
    t += 0.4
    events.append([t, "o", "\r\n"])
    base = t
    for at, line in scene["stdout"]:  # type: ignore[union-attr]
        events.append([round(base + float(at), 3), "o", line + "\r\n"])
    for line in scene["stderr"]:  # type: ignore[union-attr]
        t = round(base + float(scene["seconds"]) + 0.05, 3)
        events.append([t, "o", f"\u001b[2m{line}\u001b[0m\r\n"])
    return "\n".join([json.dumps(header), *(json.dumps(e) for e in events)]) + "\n"


def main() -> None:
    DEMOS.mkdir(parents=True, exist_ok=True)
    for name, argv, stdin_file, title in SCENES:
        print(f"recording {name} ...", file=sys.stderr, flush=True)
        scene = run_scene(name, argv, stdin_file)
        scene["title"] = title
        (DEMOS / f"{name}.json").write_text(json.dumps(scene, indent=2, ensure_ascii=False) + "\n")
        (DEMOS / f"{name}.cast").write_text(asciicast(scene, title))
        print(f"  exit {scene['exit']} in {scene['seconds']}s, {len(scene['stdout'])} lines", file=sys.stderr)  # type: ignore[arg-type]
        time.sleep(3)  # be gentle with rate limits


if __name__ == "__main__":
    main()
