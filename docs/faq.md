# FAQ and things to know

**Which key do I need?** Any one of: `TYPESAFE_API_KEY` ([console.typesafe.ai](https://console.typesafe.ai/settings/keys)),
`OPENROUTER_API_KEY` ([openrouter.ai/keys](https://openrouter.ai/keys)), `AI_GATEWAY_API_KEY`
([Vercel AI Gateway](https://vercel.com/ai-gateway), a `vck_…` key), or your own System One gateway via
`JEV_GATEWAY_URL` + `JEV_GATEWAY_API_KEY`. `jtools doctor` tells you what it found and makes one call.

**Which of those has actually been run against?** Every backend's request and response shape is
covered by the offline suite, and every endpoint above answers a keyless request with an auth
error rather than a 404, so the URLs are current. Only the **Vercel AI Gateway** was driven end to
end with a real key for the recorded benchmarks; that is the one the numbers in
[benchmarks.md](benchmarks.md) come from. If a backend misbehaves against a live key, that is a
bug worth an issue — `jtools doctor` output is the whole report.

**Is this an LLM wrapper?** No. Jev is a decision model: it never writes text. Every tool asks a
typed question (yes/no, choice, score) and gets a probability back. That is why one decision is
a single short round trip rather than a wait on generated tokens, why the same yes/no costs
<!--num:cost_ratio_range-->19.3-257.9x the cost<!--/num--> at a chat model's list price, and why the
tools compose like coreutils. The measurements, and what they do and do not cover, are in
[benchmarks.md](benchmarks.md).

**Why not embeddings?** Embeddings find *similar* text. j-tools judges *whether a description
applies*, with a calibrated probability. "Angriest customer first" is not a similarity query.

**How much does it cost?** About <!--num:tokens_per_call_round-->300<!--/num--> tokens per line, or
<!--num:dollars_per_call-->$0.000013<!--/num--> at TypeSafe's list price of $0.042 per million input
tokens (output is free). A million lines is about <!--num:dollars_per_million-->$13<!--/num-->. Every tool
stops at `--budget` (default $1) and prints `--stats` on request. Reruns are free: answers live
in `~/.cache/jev/answers.sqlite`, keyed on the model name you asked for. Each row also records
the version that answered, so when a moving alias like `jev-latest` comes to mean something else,
the first real call to notice throws the old version's answers away and says so.

**Why did the same command give a slightly different probability?** Jev is near-deterministic,
not exactly deterministic; probabilities can move by a few hundredths between uncached runs and
decisions rarely flip. Pin a model (`--model jev-1.13.0`) and keep the cache for exact reruns.

**A line with a "prompt injection" in it fooled the filter.** It can. Text in the input can try
to steer the answer. Do not use j-tools as a security boundary; use `-p 0.9` and a human where
it matters. `jgate` fails closed on API errors for the same reason.

**Rate limits.** Free-tier gateways throttle. j-tools waits them out: a 429 brakes every
request (one limit does not fan out into twenty) and requests then trickle out one at a time for
two minutes; the wait is bounded only by `--timeout` (default 15 s; set `JEV_TIMEOUT=600` and
`JEV_CONCURRENCY=4` for a slow tier). After that the line passes through unjudged and the exit
status is 5. The Vercel AI Gateway free tier, for instance, allows about five Jev requests per
three minutes; `jtools doctor` works, pipelines of a few dozen lines take minutes, and paid
credits lift the limit.

**Exit codes.** 0 ok, 1 nothing matched, 2 usage or file error, 3 no or bad key, 4 API error or
budget spent (with `--strict`, the first API error), 5 partial: some lines could not be judged
and passed through. `jgate` uses 0/1 for pass/fail and 4 for "could not judge".

**Where is `jgrep`'s `--budget`, `--stats`, cache?** All shared: every tool has `--budget`,
`--stats`, `--no-cache`, `--json`, `--dry-run`, `-p`, `-j`, `--model`, `--api`, `--timeout`,
`--max-chars`, `--strict`, `--color`.

**How high should I set `-j`?** The default of 20 suits most pipelines. It scales close to
linearly if you raise it: the same 600 lines take 150 s at `-j 1`, 22 s at `-j 8` and 5 s at
`-j 48`. Around 32-48 is where a gateway stops answering any faster, so that is the practical
ceiling for a large batch. A 10,000-line file is roughly a minute and 14 cents at the list price,
in 43 MB of memory — the streaming tools hold a bounded window, not the file.

**Does it stream?** Yes. Lines are judged the moment they arrive and printed in input order;
`tail -f app.log | jwatch …` and `tail -f … | jgrep …` work with no buffering. Tools whose nature
needs all the input (`jsort`, `jpick`, `jhead`, `juniq`, `jmatch`) read to EOF first, and hold it:
reckon on a few kilobytes per record while the run is in flight, so a few hundred thousand lines is
gigabytes. The default budget stops a run near 77,000 lines long before that bites; with
`--budget 0` the tool says what it is holding once the count passes 200,000.

**Long lines?** Records are cut at `--max-chars` (default 8,000; `jgate` uses 60,000 for whole
inputs) with a warning. Jev's context is 32k tokens per state.

**Non-English text?** Jev judges the description you write in the language you write it.
Descriptions and inputs can be in different languages.

**Windows?** Python 3.11+ anywhere; the tools are plain console scripts. Colour follows
`NO_COLOR` and `--color`.
