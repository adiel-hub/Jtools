"""Render bench/results/*.json into the SVG charts the README embeds.

    uv run python scripts/render_charts.py

Hand-written SVG: no plotting library, crisp at any size, readable in light and dark GitHub
themes. Every number comes from a results file written by a live benchmark run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "bench" / "results"
ASSETS = ROOT / "docs" / "assets"

FONT = "font-family='-apple-system, Segoe UI, Helvetica, Arial, sans-serif'"
INK, MUTED, GRID = "#24292f", "#57606a", "#d0d7de"
ACCENT, ACCENT2, ACCENT3, ACCENT4 = "#2da44e", "#0969da", "#bf8700", "#cf222e"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def hbar_chart(
    title: str, subtitle: str, rows: list[tuple[str, float, str, str]], unit: str, width: int = 760, log: bool = False
) -> str:
    """rows: (label, value, colour, annotation)."""
    import math
    import textwrap

    # The subtitle is 12px sans: about 6.4px per character inside the 40px side margins.
    caption = textwrap.wrap(subtitle, max(40, int((width - 40) / 6.4))) or [""]
    # Labels are right-aligned into their own column; 13px sans is about 6.9px per character.
    label_w = max(250, int(max(len(label) for label, *_ in rows) * 6.9) + 20)
    bar_x, row_h = label_w + 10, 34
    width = max(width, bar_x + 260)
    top = 52 + 16 * len(caption)
    height = top + row_h * len(rows) + 40
    vmax = max(v for _, v, _, _ in rows) or 1.0
    span = width - bar_x - 120

    def length(v: float) -> float:
        if log:
            lo = min(x for _, x, _, _ in rows if x > 0) / 2
            return span * (math.log10(max(v, lo)) - math.log10(lo)) / (math.log10(vmax) - math.log10(lo))
        return span * v / vmax

    out = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}' {FONT}>",
        f"<rect width='{width}' height='{height}' fill='white' rx='8'/>",
        f"<text x='20' y='30' font-size='18' font-weight='600' fill='{INK}'>{esc(title)}</text>",
    ]
    out += [
        f"<text x='20' y='{52 + 16 * i}' font-size='12' fill='{MUTED}'>{esc(line)}</text>"
        for i, line in enumerate(caption)
    ]
    for i, (label, value, colour, note) in enumerate(rows):
        y = top + i * row_h
        out.append(
            f"<text x='{label_w}' y='{y + 20}' font-size='13' fill='{INK}' text-anchor='end'>{esc(label)}</text>"
        )
        out.append(
            f"<rect x='{bar_x}' y='{y + 6}' width='{max(2, length(value)):.1f}' height='20' rx='3' fill='{colour}'/>"
        )
        out.append(
            f"<text x='{bar_x + length(value) + 8:.1f}' y='{y + 21}' font-size='12' fill='{INK}'>{esc(note)}</text>"
        )
    out.append(
        f"<text x='{width - 20}' y='{height - 14}' font-size='11' fill='{MUTED}' text-anchor='end'>{esc(unit)}</text>"
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def fmt_money(d: float) -> str:
    return f"${d:.4f}" if d >= 0.001 else f"${d:.5f}"


def cost_chart() -> None:
    data = json.loads((RESULTS / "cost.json").read_text())
    rows = []
    for r in data["rows"]:
        colour = ACCENT if r["kind"] == "decision model" else ACCENT2
        note = fmt_money(r["dollars_per_1000"]) + (
            "" if "relative_to_jev" not in r else f"  ({r['relative_to_jev']:g}x)"
        )
        rows.append((r["model"], r["dollars_per_1000"], colour, note))
    rows.sort(key=lambda t: t[1])
    svg = hbar_chart(
        "Dollars per 1,000 yes/no decisions",
        "Jev: measured tokens x list price. Chat models: same prompt size at their published per-token rates (not called).",
        rows,
        "USD per 1,000 decisions, log scale",
        log=True,
    )
    (ASSETS / "cost-per-1000.svg").write_text(svg)


def latency_chart() -> None:
    data = json.loads((RESULTS / "latency.json").read_text())
    s = data["single"]
    rows = [
        ("one yes/no question, median", s["p50_ms"], ACCENT, f"{s['p50_ms']} ms"),
        ("one yes/no question, 95th percentile", s["p95_ms"], ACCENT3, f"{s['p95_ms']} ms"),
    ]
    for b in data["batching"]:
        k = b["questions_per_call"]
        if k == 1:
            continue  # the same measurement as the first row
        rows.append((f"{k} questions about one line, median", b["p50_ms"], ACCENT2, f"{b['p50_ms']} ms"))
    svg = hbar_chart(
        "How long one decision takes",
        f"{s['n']} sequential calls through {data['backend']} ({data['model']}) on {data['when'][:10]}, uncached. "
        "Asking more questions about the same line costs tokens, not time.",
        rows,
        "milliseconds for the HTTP exchange, network included",
    )
    (ASSETS / "latency.svg").write_text(svg)
    t = [r for r in (data.get("throughput") or []) if not r.get("rate_limited")]
    if t:  # a throttled key measures the quota, not the tool
        rows = [
            (
                f"jgrep -j {r['jobs']}",
                r["lines_per_second"] or 0,
                ACCENT if r["jobs"] > 1 else ACCENT2,
                f"{r['lines_per_second']} lines/s  ({r['lines']} lines in {r['seconds']}s)",
            )
            for r in t
        ]
        svg = hbar_chart(
            "Throughput: lines judged per second",
            "the same file through the real jgrep, uncached",
            rows,
            "lines per second",
        )
        (ASSETS / "throughput.svg").write_text(svg)


def judged(d: dict[str, Any]) -> int:
    return int(d.get("judged") or d.get("lines") or 0)


def accuracy_chart() -> None:
    rows: list[tuple[str, float, str, str]] = []
    spam = RESULTS / "accuracy-spam.json"
    if spam.exists():
        d = json.loads(spam.read_text())
        rows.append(
            (
                f"jgrep finds SMS spam, F1 (n={judged(d)})",
                d["jgrep_at_0.5"]["f1"],
                ACCENT,
                f"{d['jgrep_at_0.5']['f1']:.2f}",
            )
        )
        full = d.get("keyword_grep_full_corpus")
        label = f"a 17-term keyword regex, F1 (n={full['lines']:,})" if full else "a 17-term keyword regex, F1"
        k = full or d["keyword_grep"]
        rows.append((label, k["f1"], ACCENT4, f"{k['f1']:.2f}"))
    sent = RESULTS / "accuracy-sentiment.json"
    if sent.exists():
        d = json.loads(sent.read_text())
        rows.append((f"jsort ranks review sentiment, AUC (n={judged(d)})", d["auc"], ACCENT, f"{d['auc']:.2f}"))
        rows.append(
            ("the same, precision in the top half", d["precision_at_half"], ACCENT2, f"{d['precision_at_half']:.2f}")
        )
    news = RESULTS / "accuracy-news.json"
    if news.exists():
        d = json.loads(news.read_text())
        rows.append(
            (f"jtag labels AG News 4 ways, accuracy (n={judged(d)})", d["accuracy"], ACCENT, f"{d['accuracy']:.2f}")
        )
        rows.append(("the same, macro F1", d["macro_f1"], ACCENT2, f"{d['macro_f1']:.2f}"))
    if not rows:
        return
    svg = hbar_chart(
        "Accuracy on public labelled corpora",
        "each row runs the installed command, uncached, at its default threshold, from the one-line "
        "description in docs/benchmarks.md",
        rows,
        "0 to 1; higher is better",
    )
    (ASSETS / "accuracy.svg").write_text(svg)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    made = []
    if (RESULTS / "cost.json").exists():
        cost_chart()
        made.append("cost-per-1000.svg")
    if (RESULTS / "latency.json").exists():
        latency_chart()
        made.append("latency.svg (+throughput.svg)")
    accuracy_chart()
    made.append("accuracy.svg")
    print("rendered:", ", ".join(made), file=sys.stderr)


if __name__ == "__main__":
    main()
