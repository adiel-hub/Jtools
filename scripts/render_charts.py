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
    # The annotation sits to the right of its bar, so the space it needs has to be reserved rather
    # than assumed: a fixed 120px was narrower than "115 lines/s (5s for 600)", and the longest bar
    # pushed its own label off the right edge of the canvas. 12px sans is about 6.6px per character.
    note_w = max(120, int(max(len(note) for *_, note in rows) * 7.0) + 24)  # + the 8px gap and slack
    width = max(width, bar_x + 160 + note_w)
    top = 52 + 16 * len(caption)
    height = top + row_h * len(rows) + 40
    vmax = max(v for _, v, _, _ in rows) or 1.0
    span = width - bar_x - note_w

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
        decision = r["kind"] == "decision model"
        # The reader is comparing tools, not model IDs: the row that matters is what *this project*
        # costs them per decision, so it carries the project's name and the model goes in the
        # subtitle. The chat rows keep their vendor prefix, which is the point of naming them.
        label = "j-tools" if decision else r["model"]
        note = fmt_money(r["dollars_per_1000"]) + ("" if decision else f"  ({r['relative_to_jev']:g}x more)")
        rows.append((label, r["dollars_per_1000"], ACCENT if decision else ACCENT2, note))
    rows.sort(key=lambda t: t[1])
    cheapest = min(r["relative_to_jev"] for r in data["rows"] if r["kind"] != "decision model")
    svg = hbar_chart(
        f"One decision costs {cheapest:g}x to {max(r['relative_to_jev'] for r in data['rows'] if r['kind'] != 'decision model'):g}x less with j-tools",
        "Measured tokens per decision at list price, against the same prompt priced at each chat model's "
        "published rate. Every price from the gateway catalog on the day of the run.",
        rows,
        "USD per 1,000 decisions, log scale",
        log=True,
    )
    (ASSETS / "cost-per-1000.svg").write_text(svg)


def latency_chart() -> bool:
    """The per-decision latency chart. Returns whether a throughput chart was drawn too."""
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
    many = next((b for b in reversed(data["batching"]) if b["questions_per_call"] > 1), None)
    headline = f"One decision in {s['p50_ms']} ms"
    if many:
        headline += f", {many['questions_per_call']} about the same line in {many['p50_ms']} ms"
    svg = hbar_chart(
        headline,
        "j-tools puts every question about a line into one request, so asking more of them costs "
        f"tokens rather than time. {s['n']} sequential calls, uncached, on {data['when'][:10]}.",
        rows,
        "milliseconds for the HTTP exchange, network included",
    )
    (ASSETS / "latency.svg").write_text(svg)
    return throughput_chart(data)


def throughput_chart(data: dict[str, Any]) -> bool:
    """Lines per second at -j 1 and -j 8. A throttled key measures the quota, not the tool."""
    t = [r for r in (data.get("throughput") or []) if not r.get("rate_limited")]
    if t:
        # Up to the fastest setting measured. A sweep runs past it to find where more concurrency
        # stops buying anything, but the chart is about what the flag does for you, so it shows the
        # range where raising it still does something. The full sweep stays in the results file.
        peak = max(range(len(t)), key=lambda i: t[i]["lines_per_second"] or 0)
        t = t[: peak + 1]
        fastest = t[-1]["lines_per_second"] or 0
        rows = [
            (
                f"jgrep -j {r['jobs']}",
                r["lines_per_second"] or 0,
                ACCENT if r["jobs"] > 1 else ACCENT2,
                f"{r['lines_per_second']:,.0f} lines/s",
            )
            for r in t
        ]
        svg = hbar_chart(
            f"{fastest:,.0f} lines judged per second",
            f"One flag. The same file through the real jgrep, uncached: -j raises how many decisions are "
            f"in flight at once, and {t[0]['lines']:,} lines go from {t[0]['seconds']:.0f} s to "
            f"{t[-1]['seconds']:.0f} s.",
            rows,
            "lines per second",
        )
        (ASSETS / "throughput.svg").write_text(svg)
    return bool(t)


def judged(d: dict[str, Any]) -> int:
    return int(d.get("judged") or d.get("lines") or 0)


def accuracy_chart() -> None:
    """One row per job, in the words someone would use to describe the job.

    It used to be six rows carrying three different measures -- F1, AUC, accuracy -- plus a
    secondary figure under each. A reader cannot compare an F1 with an AUC, so the chart asked
    them to know which was which before it meant anything, and the one comparison that matters
    (against a keyword filter anybody could write) was buried in the middle of it. Now: one row
    per job, plain labels, the baseline directly under the row it loses to, and the measure names
    moved to the footnote where they belong.
    """
    rows: list[tuple[str, float, str, str]] = []
    measures = []
    spam = RESULTS / "accuracy-spam.json"
    if spam.exists():
        d = json.loads(spam.read_text())
        f1 = d["jgrep_at_0.5"]["f1"]
        rows.append((f"find the spam in {judged(d):,} text messages", f1, ACCENT, f"{f1:.2f}"))
        terms = d["keyword_grep"]["regex"].count("|") + 1  # the baseline's size, not a remembered number
        k = d.get("keyword_grep_full_corpus") or d["keyword_grep"]
        rows.append((f"a {terms}-word keyword filter, same messages", k["f1"], ACCENT4, f"{k['f1']:.2f}"))
        measures.append("F1 for the spam rows")
    sent = RESULTS / "accuracy-sentiment.json"
    if sent.exists():
        d = json.loads(sent.read_text())
        rows.append((f"put {judged(d):,} reviews in order, happiest first", d["auc"], ACCENT, f"{d['auc']:.2f}"))
        measures.append("ranking quality for the reviews")
    news = RESULTS / "accuracy-news.json"
    if news.exists():
        d = json.loads(news.read_text())
        rows.append((f"sort {judged(d):,} news stories into 4 topics", d["accuracy"], ACCENT, f"{d['accuracy']:.2f}"))
        measures.append("share labelled correctly for the news")
    if not rows:
        return
    svg = hbar_chart(
        "Tell it what you want in one sentence. It gets the rest right.",
        "No examples, no training, no tuning \u2014 each row is the real command run over a whole public "
        "dataset, given nothing but a one-line description of what to look for.",
        rows,
        "1.00 is perfect: " + ", ".join(measures),
    )
    (ASSETS / "accuracy.svg").write_text(svg)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    made = []
    if (RESULTS / "cost.json").exists():
        cost_chart()
        made.append("cost-per-1000.svg")
    if (RESULTS / "latency.json").exists():
        made.append("latency.svg (+throughput.svg)" if latency_chart() else "latency.svg")
    accuracy_chart()
    made.append("accuracy.svg")
    print("rendered:", ", ".join(made), file=sys.stderr)


if __name__ == "__main__":
    main()
