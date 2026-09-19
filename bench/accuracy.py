"""How accurate are the tools on public labelled text?

    uv run python bench/accuracy.py prepare              # download corpora into bench/out/
    uv run python bench/accuracy.py spam --n 200         # jgrep vs a keyword regex (precision/recall/F1)
    uv run python bench/accuracy.py sentiment --n 120    # jsort: does the ranking put positives first?
    uv run python bench/accuracy.py news --n 120         # jtag: four-way topic labels

Each subcommand runs the installed command itself, uncached, at the default threshold, on an
evenly spaced sample of the corpus (fixed, so reruns compare). Results go to bench/results/.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.request
import zipfile

from _common import OUT, backend_info, prf, run_tool, save

SPAM_URL = "https://archive.ics.uci.edu/static/public/228/sms+spam+collection.zip"  # CC BY 4.0
SENT_URL = "https://archive.ics.uci.edu/static/public/331/sentiment+labelled+sentences.zip"  # CC BY 4.0
NEWS_URL = "https://raw.githubusercontent.com/mhjabreel/CharCnn_Keras/master/data/ag_news_csv/test.csv"

SPAM_DESCRIPTION = "an unsolicited spam, scam or marketing text message"
# A keyword filter of the kind people actually write; fixed before looking at any results.
SPAM_REGEX = r"free|win|won|prize|claim|urgent|cash|txt|text .* to|call now|reply|offer|guaranteed|£|\$|www\.|http"
NEWS_LABELS = {
    1: ("world", "news about world affairs, politics or conflict"),
    2: ("sports", "news about sports"),
    3: ("business", "news about business, markets or the economy"),
    4: ("scitech", "news about science or technology"),
}


def one_line(text: str) -> str:
    return " ".join(text.split())


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def prepare() -> None:
    OUT.mkdir(exist_ok=True)
    raw = fetch(SPAM_URL)
    rows = zipfile.ZipFile(io.BytesIO(raw)).read("SMSSpamCollection").decode("utf-8").splitlines()
    pairs = [r.split("\t", 1) for r in rows if "\t" in r]
    (OUT / "spam.txt").write_text("".join(one_line(t) + "\n" for _, t in pairs))
    (OUT / "spam.labels").write_text("".join(("1" if y == "spam" else "0") + "\n" for y, _ in pairs))
    raw = fetch(SENT_URL)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    sentences: list[tuple[str, str]] = []
    for name in zf.namelist():
        base = name.rsplit("/", 1)[-1]
        if name.startswith("__MACOSX/") or base.startswith("._"):
            continue  # AppleDouble resource forks shipped inside the zip
        if base.endswith("_labelled.txt"):
            for line in zf.read(name).decode("utf-8", "replace").splitlines():
                if "\t" in line:
                    text, label = line.rsplit("\t", 1)
                    sentences.append((one_line(text), label.strip()))
    (OUT / "sentiment.txt").write_text("".join(t + "\n" for t, _ in sentences))
    (OUT / "sentiment.labels").write_text("".join(y + "\n" for _, y in sentences))
    news = list(csv.reader(io.StringIO(fetch(NEWS_URL).decode("utf-8"))))
    (OUT / "news.txt").write_text(
        "".join(one_line(f"{title}. {body}").replace("\\", " ") + "\n" for _, title, body in news)
    )
    (OUT / "news.labels").write_text("".join(y + "\n" for y, _, _ in news))
    print(f"spam: {len(pairs):,} messages; sentiment: {len(sentences):,} sentences; news: {len(news):,} articles")


def load(name: str, n: int) -> tuple[list[str], list[int]]:
    lines = (OUT / f"{name}.txt").read_text().splitlines()
    labels = [int(x) for x in (OUT / f"{name}.labels").read_text().split()]
    assert len(lines) == len(labels), "a text spans lines; rerun prepare"
    if n and n < len(lines):
        step = len(lines) / n
        idx = [int(i * step) for i in range(n)]  # an even spread through the corpus, fixed
        lines, labels = [lines[i] for i in idx], [labels[i] for i in idx]
    return lines, labels


def spam(n: int, jobs: int) -> None:
    lines, labels = load("spam", n)
    truth = [y == 1 for y in labels]
    code, out, err, stats = run_tool(
        ["jgrep", "--json", "-p", "0", "-j", str(jobs), SPAM_DESCRIPTION],
        "".join(t + "\n" for t in lines),
    )
    if code not in (0, 1):
        sys.exit(f"jgrep failed: {err[-400:]}")
    p = {r["line"]: r["p"] for r in (json.loads(x) for x in out.splitlines()) if r.get("p") is not None}
    ps = [p.get(i + 1, 0.0) for i in range(len(lines))]
    t0 = time.perf_counter()
    keyword = [bool(re.search(SPAM_REGEX, line, re.I)) for line in lines]
    result = {
        **backend_info(),
        "dataset": "UCI SMS Spam Collection",
        "lines": len(lines),
        "positives": sum(truth),
        "description": SPAM_DESCRIPTION,
        "jgrep_at_0.5": prf([x >= 0.5 for x in ps], truth),
        "jgrep_at_0.9": prf([x >= 0.9 for x in ps], truth),
        "jgrep_run": stats,
        "keyword_grep": prf(keyword, truth) | {"regex": SPAM_REGEX, "seconds": round(time.perf_counter() - t0, 4)},
        "predictions": [
            {"text": line, "spam": t, "p": ps[i], "regex": keyword[i]}
            for i, (line, t) in enumerate(zip(lines, truth, strict=True))
        ],
    }
    save("accuracy-spam", result)
    print(json.dumps(result, indent=2))


def sentiment(n: int, jobs: int) -> None:
    lines, labels = load("sentiment", n)
    code, out, err, stats = run_tool(
        ["jsort", "--json", "-j", str(jobs), "most positive, happiest customer"],
        "".join(t + "\n" for t in lines),
    )
    if code not in (0, 1, 5):
        sys.exit(f"jsort failed: {err[-400:]}")
    rows = [json.loads(x) for x in out.splitlines()]
    truth = dict(zip(lines, labels, strict=True))
    ranked = [truth.get(r["line"], 0) for r in rows]
    positives = sum(ranked)
    # Precision at the top of the ranking, and how many pairs (pos, neg) are ordered correctly (AUC).
    top = ranked[: max(1, positives // 2)]
    pos_ranks = [i for i, y in enumerate(ranked) if y == 1]
    neg_ranks = [i for i, y in enumerate(ranked) if y == 0]
    correct_pairs = sum(1 for pr in pos_ranks for nr in neg_ranks if pr < nr)
    auc = correct_pairs / max(len(pos_ranks) * len(neg_ranks), 1)
    scores = [r["score"] for r in rows if r.get("score") is not None]
    result = {
        **backend_info(),
        "dataset": "UCI Sentiment Labelled Sentences (Amazon, IMDb, Yelp)",
        "lines": len(lines),
        "positives": positives,
        "description": "most positive, happiest customer",
        "precision_at_half": round(sum(top) / len(top), 4),
        "auc": round(auc, 4),
        "score_split_at_0.5": prf(
            [s >= 0.5 for s in scores], [truth.get(r["line"], 0) == 1 for r in rows if r.get("score") is not None]
        ),
        "jsort_run": stats,
        "ranking": [{"text": r["line"], "score": r.get("score"), "positive": truth.get(r["line"], 0)} for r in rows],
    }
    save("accuracy-sentiment", result)
    print(json.dumps(result, indent=2))


def news(n: int, jobs: int) -> None:
    lines, labels = load("news", n)
    spec = ",".join(f"{name}:{desc}" for name, desc in NEWS_LABELS.values())
    code, out, err, stats = run_tool(
        ["jtag", "--json", "-j", str(jobs), "--labels", spec], "".join(t + "\n" for t in lines)
    )
    if code not in (0, 1, 5):
        sys.exit(f"jtag failed: {err[-400:]}")
    rows = [json.loads(x) for x in out.splitlines()]
    name_of = {k: v[0] for k, v in NEWS_LABELS.items()}
    predicted = [r.get("label") for r in rows]
    truth_names = [name_of[y] for y in labels]
    per_class = {
        name: prf([p == name for p in predicted], [t == name for t in truth_names]) for name in name_of.values()
    }
    result = {
        **backend_info(),
        "dataset": "AG News test split",
        "lines": len(lines),
        "labels": spec,
        "accuracy": round(sum(p == t for p, t in zip(predicted, truth_names, strict=True)) / len(lines), 4),
        "macro_f1": round(sum(v["f1"] for v in per_class.values()) / len(per_class), 4),
        "per_class": per_class,
        "jtag_run": stats,
        "predictions": [
            {"text": r["line"][:200], "truth": t, "predicted": r.get("label"), "probabilities": r.get("probabilities")}
            for r, t in zip(rows, truth_names, strict=True)
        ],
    }
    save("accuracy-news", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["prepare", "spam", "sentiment", "news"])
    ap.add_argument("--n", type=int, default=0, help="sample size (0 = whole corpus)")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    if a.what == "prepare":
        prepare()
    else:
        {"spam": spam, "sentiment": sentiment, "news": news}[a.what](a.n, a.jobs)
