"""Dollars per 1,000 decisions: Jev, measured, next to chat models at their list prices.

    uv run python bench/cost.py

Jev's tokens per decision come from `bench/results/latency.json` (a real run). Chat-model rows
are arithmetic: the same prompt (a system instruction plus the line, about the same token count)
and a one-word answer, priced with each model's published per-token rates as listed by the
Vercel AI Gateway catalog on the date recorded. No chat model is called; this is a price table,
not a race. Latency for chat models is deliberately absent (not measured here).
"""

from __future__ import annotations

import json
from pathlib import Path

from _common import RESULTS, save

# (model id, input $/token, output $/token) as published in the gateway catalog, 2026-09-18.
CHAT_MODELS = [
    ("openai/gpt-5.6-luna", 0.0000002, 0.0000012),
    ("openai/gpt-5.4-mini", 0.00000075, 0.0000045),
    ("google/gemini-3.8-flash", 0.00000075, 0.00000375),
    ("anthropic/claude-haiku-4.5", 0.000001, 0.000005),
    ("alibaba/qwen3.8-flash", 0.00000015, 0.00000047),
    ("openai/gpt-5.5", 0.000005, 0.00003),
]
JEV_INPUT_PER_TOKEN = 0.042 / 1e6
ANSWER_TOKENS = 5  # "yes" or "no" plus stop; reasoning models would spend far more


def main() -> None:
    latency_path = RESULTS / "latency.json"
    if latency_path.exists():
        latency = json.loads(latency_path.read_text())
        jev_tokens = int(latency["single"]["tokens_per_call"])
        jev_measured = float(latency["single"]["dollars_per_1000"]) or jev_tokens * JEV_INPUT_PER_TOKEN * 1000
        source = "measured tokens (bench/results/latency.json) x list price"
    else:
        jev_tokens, jev_measured, source = 300, 300 * JEV_INPUT_PER_TOKEN * 1000, "estimate (300 tokens)"
    rows = [
        {
            "model": "typesafe-ai/jev",
            "kind": "decision model",
            "prompt_tokens": jev_tokens,
            "output_tokens": 0,
            "dollars_per_1000": round(jev_measured, 5),
            "source": source,
        }
    ]
    for model, pin, pout in CHAT_MODELS:
        dollars = (jev_tokens * pin + ANSWER_TOKENS * pout) * 1000
        rows.append(
            {
                "model": model,
                "kind": "chat model",
                "prompt_tokens": jev_tokens,
                "output_tokens": ANSWER_TOKENS,
                "dollars_per_1000": round(dollars, 5),
                "relative_to_jev": round(dollars / jev_measured, 1) if jev_measured else None,
                "source": "list price arithmetic",
            }
        )
    save("cost", {"note": __doc__.strip().splitlines()[0], "rows": rows})
    width = max(len(r["model"]) for r in rows)
    for r in rows:
        rel = f"  x{r['relative_to_jev']}" if "relative_to_jev" in r else "  (baseline)"
        print(f"{r['model']:<{width}}  ${r['dollars_per_1000']:.5f} / 1,000 decisions{rel}")


if __name__ == "__main__":
    Path(RESULTS).mkdir(exist_ok=True)
    main()
