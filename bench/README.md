# Benchmarks

Everything here runs the **installed commands** against a **real** Jev endpoint, uncached, so the
numbers are what a user gets. Results land in `bench/results/*.json` and the charts in
`docs/assets/` are rendered from them by `scripts/render_charts.py`.

```bash
uv run python bench/latency.py                  # per-call latency, tokens, dollars; batching; concurrency
uv run python bench/accuracy.py prepare         # download the public corpora into bench/out/
uv run python bench/accuracy.py spam            # jgrep vs a keyword regex on SMS spam (precision/recall/F1)
uv run python bench/accuracy.py sentiment       # jsort ranking quality on labelled sentences
uv run python bench/accuracy.py news            # jtag four-way classification on AG News
uv run python bench/cost.py                     # dollars per 1,000 decisions: Jev vs chat models at list price
uv run python scripts/render_charts.py          # docs/assets/*.svg from bench/results/*.json
```

Each script takes `--n` to size the sample (the defaults fit a free-tier key's rate limit) and
records the backend, model and timestamp in its result file. The whole set costs a few cents.

## Corpora

| corpus | licence | used by |
|---|---|---|
| [UCI SMS Spam Collection](https://archive.ics.uci.edu/dataset/228/sms+spam+collection) | CC BY 4.0 | `spam` |
| [UCI Sentiment Labelled Sentences](https://archive.ics.uci.edu/dataset/331/sentiment+labelled+sentences) | CC BY 4.0 | `sentiment` |
| [AG News](http://groups.di.unipi.it/~gulli/AG_corpus_of_news_articles.html) test split | research use | `news` |

## Method notes

- Latency is wall time of one HTTP round trip from the client, so it includes the network and
  the gateway. The provider's own inference time is a fraction of it.
- Cost for TypeSafe is computed from reported input tokens at the list price of $0.042 per
  million; the Vercel gateway and OpenRouter report dollars directly and those are used as is.
- Chat-model costs in `cost.py` are **list-price arithmetic**, not measured calls: the same prompt
  size in tokens, priced with each model's published per-token rates. Chat-model *latency* is
  not measured here; jgrep's published measurement (800-1000 ms per yes/no decision for GPT-class
  models through OpenRouter) is cited in the README as an external reference.
