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


def judged(result: dict[str, Any]) -> int:
    """How many records the run actually got an answer for."""
    return int(result.get("judged") or result.get("lines") or 0)


def coverage(result: dict[str, Any], noun: str) -> str:
    """ "all 5,574 messages" or "1,200 of 5,574 sampled messages judged".

    A run over the whole corpus is a stronger claim than a sample of it, and printing it as
    "5574 of 5574 sampled messages judged" states the weaker one while also being hard to read.
    """
    got, total = judged(result), int(result.get("lines") or 0)
    if got == total:
        return f"all {total:,} {noun}"
    return f"{got:,} of {total:,} sampled {noun} judged"


def load(name: str) -> dict[str, Any] | None:
    path = RESULTS / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


LIST_PRICE_PER_TOKEN = 0.042 / 1e6


def money(d: float) -> str:
    return f"${d:.4f}" if d >= 0.001 else f"${d:.6f}"


def duration(ms: Any) -> str:
    """Milliseconds until they stop reading as milliseconds; a throttled wait is a queue, not a
    measurement of Jev, and "298804 ms" in a latency table reads as a five-minute answer."""
    if not isinstance(ms, int | float):
        return "?"
    return f"{ms / 1000:,.0f} s" if ms >= 10_000 else f"{ms:,.0f} ms"


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
        f"| input tokens per call | {s['tokens_per_call']} |",
        f"| dollars per call | {money(s['dollars_per_call'])} |",
        f"| dollars per 1,000 decisions | {money(s['dollars_per_1000'])} |",
    ]
    # Only when the key was actually throttled: on an unthrottled one this row repeats the two
    # above it to the millisecond, and a table should not spend a line saying nothing.
    wall_p95 = s.get("wall_p95_ms")
    if isinstance(wall_p95, int | float) and wall_p95 > s["p95_ms"] * 1.2:
        rows.insert(
            4,
            f"| the same, including the time this key spent rate-limited (p50 / p95) | "
            f"{duration(s.get('wall_p50_ms'))} / {duration(wall_p95)} |",
        )
    for b in d.get("batching", []):
        rows.append(
            f"| {b['questions_per_call']} question{'' if b['questions_per_call'] == 1 else 's'} "
            f"in one call, p50 | {b['p50_ms']} ms "
            f"({b['tokens_per_call']} tokens, {money(b['dollars_per_call'])}) |"
        )
    for t in d.get("throughput", []):
        lps = t.get("lines_per_second")
        if not t.get("rate_limited"):
            rows.append(f"| jgrep -j {t['jobs']}, {t['lines']:,} lines | {t.get('seconds')} s ({lps:,.0f} lines/s) |")
    build = f", jev-tools {d['build']}" if d.get("build") else ""
    head = (
        f"Measured {d['when'][:10]} through **{d['backend']}** (`{d['model']}`{build}), "
        f"{s['n']} sequential calls, uncached."
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
        full = spam.get("keyword_grep_full_corpus")
        run = spam["jgrep_run"]
        parts.append(
            f"### jgrep vs a keyword regex: {spam['dataset']} "
            f"({coverage(spam, 'messages')}, {spam['positives']:,} spam)\n\n"
            f"Description: *{spam['description']}*\n\n"
            "| filter | precision | recall | F1 | time | cost |\n|---|---:|---:|---:|---:|---:|\n"
            f"| `jgrep` at p ≥ 0.5 | {j['precision']:.2f} | {j['recall']:.2f} | **{j['f1']:.2f}** | {run.get('seconds', '?')} s | {money(run_dollars(run))} |\n"
            f"| `jgrep` at p ≥ 0.9 | {j9['precision']:.2f} | {j9['recall']:.2f} | {j9['f1']:.2f} | | |\n"
            f"| the same {k['regex'].count('|') + 1}-term keyword regex | {k['precision']:.2f} | {k['recall']:.2f} | {k['f1']:.2f} | | free |\n"
            + (
                f"| the same regex over all {full['lines']:,} messages | {full['precision']:.2f} | {full['recall']:.2f} "
                f"| {full['f1']:.2f} | {full['seconds']} s | free |\n"
                # Not when the sample was the whole corpus: the row above already is this row.
                if full and full["lines"] != judged(spam)
                else ""
            )
        )
    sent = load("accuracy-sentiment")
    if sent:
        run = sent["jsort_run"]
        parts.append(
            f"### jsort ranking quality: {sent['dataset']} "
            f"({coverage(sent, 'sentences')}, {sent['positives']:,} positive)\n\n"
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
        # One --label per class is a list; the older runs recorded a single --labels string.
        spec = news["labels"]
        labels = ", ".join(f"`{one}`" for one in spec) if isinstance(spec, list) else f"`{spec}`"
        parts.append(
            f"### jtag four-way classification: {news['dataset']} "
            f"({coverage(news, 'articles')})\n\n"
            f"Labels: {labels}\n\n"
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
                f"({', '.join(r['model'].split('/')[-1] for r in chat[:3])}, …). "
                "Their latency is not measured here; see the method notes."
            )
    acc = []
    if spam:
        full = spam.get("keyword_grep_full_corpus")
        terms = spam["keyword_grep"]["regex"].count("|") + 1
        baseline = (full or spam["keyword_grep"])["f1"]
        acc.append(
            f"`jgrep` finds SMS spam with F1 {spam['jgrep_at_0.5']['f1']:.2f} over {coverage(spam, 'messages')}, "
            f"where a {terms}-term keyword regex scores {baseline:.2f} on the same text"
        )
    if sent:
        acc.append(f"`jsort` ranks review sentiment with AUC {sent['auc']:.2f} over {coverage(sent, 'sentences')}")
    if news:
        acc.append(
            f"`jtag` labels AG News four ways with {news['accuracy']:.0%} accuracy over {coverage(news, 'articles')}"
        )
    if acc:
        lines.append("- **Accuracy, from a one-line description, with no tuning:** " + "; ".join(acc) + ".")
        unjudged = sum(int(r.get("unjudged") or 0) for r in (spam, sent, news) if r)
        if unjudged:
            lines.append(
                f"- **Measured on a free-tier key:** {unjudged} more records hit its rate limit and were "
                "left unjudged; they are reported, not counted as mistakes."
            )
    return "\n".join(lines) + "\n"


def numbers() -> dict[str, str]:
    """The measured figures that appear mid-sentence in the prose, keyed by placeholder name.

    A number in a paragraph drifts from the benchmark that produced it the moment either changes,
    so the prose carries ``<!--num:key-->…<!--/num-->`` spans and `scripts/update_docs.py` fills
    them from here. Nothing in the docs is a figure somebody remembered.
    """
    out: dict[str, str] = {}
    if lat := load("latency"):
        single = lat["single"]
        out["latency_p50"] = f"{single['p50_ms']} ms"
        out["latency_p50_round"] = f"{round(single['p50_ms'] / 10) * 10} ms"
        out["tokens_per_call"] = str(single["tokens_per_call"])
        out["tokens_per_call_round"] = str(round(single["tokens_per_call"] / 10) * 10)
        out["dollars_per_call"] = money(single["dollars_per_call"])
        out["dollars_per_1000"] = money(single["dollars_per_1000"])
        out["dollars_per_million"] = f"${single['dollars_per_1000'] * 1000:,.0f}"
    if cost := load("cost"):
        chat = [r for r in cost["rows"] if r["kind"] == "chat model"]
        if chat:
            lo, hi = min(r["relative_to_jev"] for r in chat), max(r["relative_to_jev"] for r in chat)
            out["cost_ratio_range"] = f"{lo:g}-{hi:g}x the cost"
    return out


def main(argv: list[str]) -> None:
    if argv[:1] == ["summary"]:
        sys.stdout.write(summary())
        return
    if argv[:1] == ["numbers"]:
        for key, value in numbers().items():
            sys.stdout.write(f"{key}\t{value}\n")
        return
    for block in (latency_table(), cost_table(), accuracy_table()):
        if block:
            sys.stdout.write(block + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
