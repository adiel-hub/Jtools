# Benchmarks

Every number here was produced by the scripts in [`bench/`](../bench/) running the **installed
commands** against a **real** endpoint, uncached, and is stored in `bench/results/*.json` with
the backend, model and timestamp. The charts in `docs/assets/` are rendered from those files by
`scripts/render_charts.py`. Reproduce with any key:

```bash
uv run python bench/latency.py
uv run python bench/accuracy.py prepare && uv run python bench/accuracy.py spam --n 200
uv run python bench/accuracy.py sentiment --n 120 && uv run python bench/accuracy.py news --n 120
uv run python bench/cost.py && uv run python scripts/render_charts.py
```

## Results

Result files land in `bench/results/`; the tables below are regenerated from them after each recorded run.

## Method notes

- **Latency** is client wall time for one HTTP round trip, so it includes the network and the
  gateway. The provider-side inference time reported in gateway metadata is a fraction of that.
- **Cost** for TypeSafe is reported input tokens at the list price of $0.042 per million (output
  tokens are free). The Vercel gateway reports dollars per call and those are used as is.
- **Chat-model cost rows are arithmetic, not measurements**: the same prompt size in tokens
  priced at each model's published per-token rates from the gateway catalog on the date shown,
  plus a five-token answer. Reasoning models would spend far more output tokens than that.
- **Chat-model latency is not measured here.** As an external reference,
  [jgrep's published benchmark](https://github.com/keltokhy/jgrep#how-well-does-it-work) measured
  800-1000 ms median per yes/no decision for GPT-class chat models through OpenRouter against
  about 210 ms for Jev, on the same messages.
- **Accuracy** samples are evenly spaced through each corpus (fixed indices), judged at the default
  threshold of 0.5, with no prompt tuning beyond the one-line description shown.
- The key used for the recorded runs was a **free-tier** Vercel AI Gateway key, which is throttled
  to a handful of requests per few minutes. That caps sample sizes and inflates wall-clock times
  and p95 latencies (a throttled call waits, then succeeds). Per-call p50 latency and per-token
  cost are unaffected; throughput figures are not representative of a paid key.
