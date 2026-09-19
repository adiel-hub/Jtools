"""Shared bits for the benchmark scripts: paths, the client, result files."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from jevcore.auth import resolve

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
RESULTS = ROOT / "results"


def build() -> str:
    """Which build produced a result: the package version, and the commit when there is a checkout."""
    from jevcore import __version__

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT.parent, check=False
        )
        commit = sha.stdout.strip() if sha.returncode == 0 else ""
    except OSError:
        commit = ""
    return f"{__version__}+{commit}" if commit else __version__


def backend_info() -> dict[str, Any]:
    creds = resolve()
    return {
        "backend": creds.backend.name,
        "url": creds.url,
        "model": os.environ.get("JEV_MODEL") or creds.backend.model,
        "build": build(),
        "when": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }


def save(name: str, result: dict[str, Any]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {path.relative_to(ROOT.parent)}", file=sys.stderr)
    return path


def run_tool(argv: list[str], stdin: str, *, timeout: float | None = None) -> tuple[int, str, str, dict[str, Any]]:
    """Run an installed j-tool with --json --stats --no-cache; parse the stats line.

    No timeout by default: a throttled key can legitimately take an hour for a few dozen lines.
    """
    cmd = [*argv, "--no-cache", "--stats", "--budget", "0"]
    proc = subprocess.run(cmd, input=stdin, text=True, capture_output=True, timeout=timeout, check=False)
    stats = parse_stats(proc.stderr)
    return proc.returncode, proc.stdout, proc.stderr, stats


def parse_stats(stderr: str) -> dict[str, Any]:
    """``jgrep: 994 calls; 0 cached; 292,839 tokens; $0.0123; p50 210 ms; 4.6s`` -> numbers."""
    import re

    line = next((ln for ln in reversed(stderr.splitlines()) if " calls" in ln and "$" in ln), "")
    out: dict[str, Any] = {}
    if m := re.search(r"([\d,]+) calls", line):
        out["calls"] = int(m.group(1).replace(",", ""))
    if m := re.search(r"([\d,]+) tokens", line):
        out["tokens"] = int(m.group(1).replace(",", ""))
    if m := re.search(r"\$([\d.]+)", line):
        out["dollars"] = float(m.group(1))
    if m := re.search(r"p50 (\d+) ms", line):
        out["p50_ms"] = int(m.group(1))
    if m := re.search(r"([\d.]+)s\s*$", line):
        out["seconds"] = float(m.group(1))
    if m := re.search(r"(\d+) rate-limited", line):
        out["rate_limited"] = int(m.group(1))
    return out


def prf(predicted: list[bool], truth: list[bool]) -> dict[str, float]:
    tp = sum(p and t for p, t in zip(predicted, truth, strict=True))
    fp = sum(p and not t for p, t in zip(predicted, truth, strict=True))
    fn = sum(t and not p for p, t in zip(predicted, truth, strict=True))
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / max(precision + recall, 1e-9), 4),
        "accuracy": round(sum(p == t for p, t in zip(predicted, truth, strict=True)) / max(len(truth), 1), 4),
    }
