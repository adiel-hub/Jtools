# FAQ and things to know

**Which key do I need?** Any one of: `TYPESAFE_API_KEY` ([console.typesafe.ai](https://console.typesafe.ai/settings/keys)),
`OPENROUTER_API_KEY` ([openrouter.ai/keys](https://openrouter.ai/keys)), `AI_GATEWAY_API_KEY`
([Vercel AI Gateway](https://vercel.com/ai-gateway), a `vck_…` key), or your own System One gateway via
`JEV_GATEWAY_URL` + `JEV_GATEWAY_API_KEY`. `jtools doctor` tells you what it found and makes one call.

**Is this an LLM wrapper?** No. Jev is a decision model: it never writes text. Every tool asks a
typed question (yes/no, choice, score) and gets a probability back. That is why it is 20-200x
faster and 40-400x cheaper than asking a chat model the same thing, and why the tools compose
like coreutils.

**Why not embeddings?** Embeddings find *similar* text. j-tools judges *whether a description
applies*, with a calibrated probability. "Angriest customer first" is not a similarity query.

**How much does it cost?** About 300 tokens per line, $0.0000126 at TypeSafe's list price
($0.042 per million input tokens; output is free). A million lines is about $13. Every tool
stops at `--budget` (default $1) and prints `--stats` on request. Reruns are free: answers are
cached in `~/.cache/jev/answers.sqlite`.

**Why did the same command give a slightly different probability?** Jev is near-deterministic,
not exactly deterministic; probabilities can move by a few hundredths between uncached runs and
decisions rarely flip. Pin a model (`--model jev-1.13.0`) and keep the cache for exact reruns.

**A line with a "prompt injection" in it fooled the filter.** It can. Text in the input can try
to steer the answer. Do not use j-tools as a security boundary; use `-p 0.9` and a human where
it matters. `jgate` fails closed on API errors for the same reason.

**Rate limits.** Free-tier gateways throttle. j-tools waits them out: a 429 brakes every
request (one limit does not fan out into twenty), and the wait is bounded only by `--timeout`
(default 15 s; set `JEV_TIMEOUT=600` for a slow tier). After that the line passes through
unjudged and the exit status is 5.

**Exit codes.** 0 ok, 1 nothing matched, 2 usage or file error, 3 no or bad key, 4 API error or
budget spent (with `--strict`, the first API error), 5 partial: some lines could not be judged
and passed through. `jgate` uses 0/1 for pass/fail and 4 for "could not judge".

**Where is `jgrep`'s `--budget`, `--stats`, cache?** All shared: every tool has `--budget`,
`--stats`, `--no-cache`, `--json`, `--dry-run`, `-p`, `-j`, `--model`, `--api`, `--timeout`,
`--max-chars`, `--strict`, `--color`.

**Does it stream?** Yes. Lines are judged the moment they arrive and printed in input order;
`tail -f app.log | jwatch …` and `tail -f … | jgrep …` work with no buffering. Tools whose nature
needs all the input (`jsort`, `jpick`, `jhead`, `juniq`, `jmatch`) read to EOF first.

**Long lines?** Records are cut at `--max-chars` (default 8,000; `jgate` uses 60,000 for whole
inputs) with a warning. Jev's context is 32k tokens per state.

**Non-English text?** Jev judges the description you write in the language you write it.
Descriptions and inputs can be in different languages.

**Windows?** Python 3.11+ anywhere; the tools are plain console scripts. Colour follows
`NO_COLOR` and `--color`.
