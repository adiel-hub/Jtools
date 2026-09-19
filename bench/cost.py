"""Dollars per 1,000 decisions: Jev, measured, next to chat models at their published prices.

    uv run python bench/cost.py

Jev's tokens per decision come from `bench/results/latency.json`, a real run. Every per-token
price, Jev's included, is read from the Vercel AI Gateway's own model catalog
(`GET /v1/models`) at the moment the benchmark runs, so no price here is typed by hand and a
rerun refreshes them all. The chat-model rows are arithmetic on those prices: the same prompt
(a system instruction plus the line, about the same token count) and a one-word answer. No chat
model is called, and their latency is deliberately absent; this is a price table, not a race.

Without a key or a network the recorded prices in `bench/results/cost.json` are reused and the
result says so, so the table can still be rebuilt offline.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import httpx

from _common import RESULTS, save

CATALOG_URL = "https://ai-gateway.vercel.sh/v1/models"
JEV_MODEL = "typesafe-ai/jev"

# A spread, not a shortlist: the cheapest small model in the catalog, the mid-tier models people
# actually reach for, and one frontier model, so the ratio is a range rather than one flattering
# comparison. Anything the catalog no longer lists is dropped and reported.
CHAT_MODELS = [
    "alibaba/qwen3.8-flash",
    "openai/gpt-5.6-luna",
    "google/gemini-3.8-flash",
    "openai/gpt-5.4-mini",
    "anthropic/claude-haiku-4.5",
    "openai/gpt-5.5",
]
ANSWER_TOKENS = 5  # "yes" or "no" plus a stop token; a reasoning model would spend far more
FALLBACK_JEV_INPUT = 0.042 / 1e6  # TypeSafe's list price, used only when the catalog is unreachable


def catalog_key() -> str | None:
    for name in ("AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_API_KEY"):
        if key := os.environ.get(name):
            return key
    return None


def fetch_prices() -> tuple[dict[str, tuple[float, float]], str]:
    """``{model: (input $/token, output $/token)}`` from the gateway catalog, plus where it came from."""
    key = catalog_key()
    if key:
        try:
            response = httpx.get(CATALOG_URL, headers={"Authorization": f"Bearer {key}"}, timeout=60)
            response.raise_for_status()
            prices = {
                item["id"]: (float(item["pricing"]["input"]), float(item["pricing"].get("output") or 0))
                for item in response.json()["data"]
                if item.get("pricing", {}).get("input") is not None
            }
            when = dt.datetime.now(dt.UTC).date().isoformat()
            return prices, f"Vercel AI Gateway catalog, fetched {when}"
        except (httpx.HTTPError, KeyError, ValueError) as e:
            print(f"catalog unreachable ({type(e).__name__}); falling back to recorded prices")
    recorded = RESULTS / "cost.json"
    if recorded.exists():
        old = json.loads(recorded.read_text())
        prices = {r["model"]: (r["input_per_token"], r.get("output_per_token", 0.0)) for r in old["rows"]}
        return prices, f"{old.get('prices_from', 'recorded prices')} (reused offline)"
    return {JEV_MODEL: (FALLBACK_JEV_INPUT, 0.0)}, "TypeSafe list price (no catalog, no recorded run)"


def jev_usage() -> tuple[int, float | None, str]:
    """Tokens per decision and measured dollars per 1,000, from the latency run when there is one."""
    path = RESULTS / "latency.json"
    if not path.exists():
        return 300, None, "estimate (300 tokens; run bench/latency.py for a measured figure)"
    latency = json.loads(path.read_text())
    tokens = int(latency["single"]["tokens_per_call"])
    measured = float(latency["single"]["dollars_per_1000"]) or None
    return tokens, measured, "measured tokens (bench/results/latency.json)"


def main() -> None:
    prices, prices_from = fetch_prices()
    tokens, measured, token_source = jev_usage()
    jev_in, _ = prices.get(JEV_MODEL, (FALLBACK_JEV_INPUT, 0.0))
    # A free tier bills nothing; price the same tokens at list so the comparison stays honest.
    jev_dollars = measured or tokens * jev_in * 1000
    missing = [m for m in CHAT_MODELS if m not in prices]

    rows: list[dict[str, Any]] = [
        {
            "model": JEV_MODEL,
            "kind": "decision model",
            "prompt_tokens": tokens,
            "output_tokens": 0,
            "input_per_token": jev_in,
            "output_per_token": 0.0,
            "dollars_per_1000": round(jev_dollars, 5),
            "source": f"{token_source} x catalog price",
        }
    ]
    for model in CHAT_MODELS:
        if model not in prices:
            continue
        pin, pout = prices[model]
        dollars = (tokens * pin + ANSWER_TOKENS * pout) * 1000
        rows.append(
            {
                "model": model,
                "kind": "chat model",
                "prompt_tokens": tokens,
                "output_tokens": ANSWER_TOKENS,
                "input_per_token": pin,
                "output_per_token": pout,
                "dollars_per_1000": round(dollars, 5),
                "relative_to_jev": round(dollars / jev_dollars, 1) if jev_dollars else None,
                "source": "catalog price arithmetic",
            }
        )

    save(
        "cost",
        {
            "note": "dollars per 1,000 yes/no decisions on the same line; chat models priced, not called",
            "prices_from": prices_from,
            "answer_tokens_assumed": ANSWER_TOKENS,
            "models_not_in_catalog": missing,
            "rows": rows,
        },
    )
    print(f"prices: {prices_from}")
    if missing:
        print(f"not in the catalog any more, dropped: {', '.join(missing)}")
    width = max(len(r["model"]) for r in rows)
    for r in rows:
        rel = f"  x{r['relative_to_jev']}" if "relative_to_jev" in r else "  (baseline)"
        print(f"{r['model']:<{width}}  ${r['dollars_per_1000']:.5f} / 1,000 decisions{rel}")


if __name__ == "__main__":
    Path(RESULTS).mkdir(exist_ok=True)
    main()
