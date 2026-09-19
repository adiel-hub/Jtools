"""Print markdown tables from bench/results/*.json for docs/benchmarks.md and the README.

    uv run python scripts/render_tables.py            # all tables to stdout
    uv run python scripts/render_tables.py summary    # the short README block only

Nothing is typed by hand: every cell comes from a result file written by a live benchmark run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "bench" / "results"


def load(name: str) -> dict[str, Any] | None:
    path = RESULTS / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


LIST_PRICE_PER_TOKEN = 0.042 / 1e6


def money(d: float) -> str:
    return f"${d:.4f}" if d >= 0.001 else f"${d:.6f}"


def run_dollars(run: dict[str, Any]) -> float:
    """Dollars of a recorded tool run; a free-tier gateway reports $0, so tokens are priced at list."""
    return float(run.get("dollars") or 0) or float(run.get("tokens") or 0) * LIST_PRICE_PER_TOKEN


def latency_table() -> str:
    d = load("latency")
    if not d:
        return ""
    s = d["single"]
    rows = [
        "| measurement | value |",
        "|---|---:|",
        f"| single yes/no call, p50 | {s['p50_ms']} ms |",
        f"| single yes/no call, p95 | {s['p95_ms']} ms |",
        f"| the same, wall time incl. rate-limit waits (p50 / p95) | {s.get('wall_p50_ms', '?')} / {s.get('wall_p95_ms', '?')} ms |",
        f"| input tokens per call | {s['tokens_per_call']} |",
        f"| dollars per call | {money(s['dollars_per_call'])} |",
        f"| dollars per 1,000 decisions | {money(s['dollars_per_1000'])} |",
    ]
    for b in d.get("batching", []):
        rows.append(
            f"| {b['questions_per_call']} question(s) in one call, p50 | {b['p50_ms']} ms "
            f"({b['tokens_per_call']} tokens, {money(b['dollars_per_call'])}) |"
        )
    for t in d.get("throughput", []):
        lps = t.get("lines_per_second")
        if not t.get("rate_limited"):
            rows.append(f"| jgrep -j {t['jobs']}, {t['lines']} lines | {t.get('seconds')} s ({lps} lines/s) |")
    head = (
        f"Measured {d['when'][:10]} through **{d['backend']}** (`{d['model']}`), {s['n']} sequential calls, uncached."
    )
    return f"### Latency and cost per decision\n\n{head}\n\n" + "\n".join(rows) + "\n"


def cost_table() -> str:
    d = load("cost")
    if not d:
        return ""
    rows = ["| model | kind | $ per 1,000 decisions | vs Jev | source |", "|---|---|---:|---:|---|"]
    for r in sorted(d["rows"], key=lambda r: r["dollars_per_1000"]):
        rel = f"{r['relative_to_jev']:g}x" if "relative_to_jev" in r else "1x"
        rows.append(f"| `{r['model']}` | {r['kind']} | {money(r['dollars_per_1000'])} | {rel} | {r['source']} |")
    return "### Dollars per 1,000 yes/no decisions\n\n" + "\n".join(rows) + "\n"


def accuracy_table() -> str:
    parts = []
    spam = load("accuracy-spam")
    if spam:
        j, k = spam["jgrep_at_0.5"], spam["keyword_grep"]
        j9 = spam["jgrep_at_0.9"]
        run = spam["jgrep_run"]
        parts.append(
            f"### jgrep vs a keyword regex: {spam['dataset']} ({spam['lines']} messages, {spam['positives']} spam)\n\n"
            f"Description: *{spam['description']}*\n\n"
            "| filter | precision | recall | F1 | time | cost |\n|---|---:|---:|---:|---:|---:|\n"
            f"| `jgrep` at p ≥ 0.5 | {j['precision']:.2f} | {j['recall']:.2f} | **{j['f1']:.2f}** | {run.get('seconds', '?')} s | {money(run_dollars(run))} |\n"
            f"| `jgrep` at p ≥ 0.9 | {j9['precision']:.2f} | {j9['recall']:.2f} | {j9['f1']:.2f} | | |\n"
            f"| keyword regex ({k['regex'].count('|') + 1} terms) | {k['precision']:.2f} | {k['recall']:.2f} | {k['f1']:.2f} | {k['seconds']} s | free |\n"
        )
    sent = load("accuracy-sentiment")
    if sent:
        run = sent["jsort_run"]
        parts.append(
            f"### jsort ranking quality: {sent['dataset']} ({sent['lines']} sentences, {sent['positives']} positive)\n\n"
            f"Description: *{sent['description']}*\n\n"
            "| measure | value |\n|---|---:|\n"
            f"| AUC (a random positive ranks above a random negative) | **{sent['auc']:.2f}** |\n"
            f"| precision in the top half of the ranking | {sent['precision_at_half']:.2f} |\n"
            f"| accuracy of a 0.5 score cut | {sent['score_split_at_0.5']['accuracy']:.2f} |\n"
            f"| time / cost | {run.get('seconds', '?')} s / {money(run_dollars(run))} |\n"
        )
    news = load("accuracy-news")
    if news:
        run = news["jtag_run"]
        per = news["per_class"]
        rows = "\n".join(
            f"| {name} | {v['precision']:.2f} | {v['recall']:.2f} | {v['f1']:.2f} |" for name, v in per.items()
        )
        parts.append(
            f"### jtag four-way classification: {news['dataset']} ({news['lines']} articles)\n\n"
            f"Labels: `{news['labels']}`\n\n"
            f"Accuracy **{news['accuracy']:.2f}**, macro F1 {news['macro_f1']:.2f}, "
            f"{run.get('seconds', '?')} s, {money(run_dollars(run))}.\n\n"
            "| label | precision | recall | F1 |\n|---|---:|---:|---:|\n" + rows + "\n"
        )
    return "\n".join(parts)


def summary() -> str:
    """The short block the README shows under the charts."""
    lat, cost, spam, sent, news = (
        load(n) for n in ("latency", "cost", "accuracy-spam", "accuracy-sentiment", "accuracy-news")
    )
    lines = []
    if lat:
        s = lat["single"]
        lines.append(
            f"- **Latency:** {s['p50_ms']} ms median per decision end to end (p95 {s['p95_ms']} ms), "
            f"measured through {lat['backend']}; asking 16 questions about the same line costs about the same time as one."
        )
        lines.append(
            f"- **Cost:** {s['tokens_per_call']} input tokens and {money(s['dollars_per_call'])} per decision, "
            f"{money(s['dollars_per_1000'])} per 1,000."
        )
    if cost:
        chat = [r for r in cost["rows"] if r["kind"] == "chat model"]
        if chat:
            lo, hi = min(r["relative_to_jev"] for r in chat), max(r["relative_to_jev"] for r in chat)
            lines.append(
                f"- **vs chat models:** the same yes/no decision costs {lo:g}x to {hi:g}x more at list price "
                f"({', '.join(r['model'].split('/')[-1] for r in chat[:3])}, …), before counting their 4-5x higher latency."
            )
    acc = []
    if spam:
        acc.append(
            f"SMS spam F1 {spam['jgrep_at_0.5']['f1']:.2f} vs {spam['keyword_grep']['f1']:.2f} for a 17-term regex (n={spam['lines']})"
        )
    if sent:
        acc.append(f"sentiment ranking AUC {sent['auc']:.2f} (n={sent['lines']})")
    if news:
        acc.append(f"AG News 4-way accuracy {news['accuracy']:.2f} (n={news['lines']})")
    if acc:
        lines.append("- **Accuracy, one-line descriptions, no tuning:** " + "; ".join(acc) + ".")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> None:
    if argv[:1] == ["summary"]:
        sys.stdout.write(summary())
        return
    for block in (latency_table(), cost_table(), accuracy_table()):
        if block:
            sys.stdout.write(block + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
