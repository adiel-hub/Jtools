# Architecture

j-tools is one small library (`jevcore`) and ten thin commands (`jevtools`). Every command reads
records, asks Jev one typed question per record (or per group of records), and acts on the
typed answer. Nothing here generates text.

```
 stdin / files ──▶ inputs.iter_records ──▶ pipeline.Pipeline ──▶ deliver (in input order) ──▶ stdout
                        │                       │
                        │                  judge(record)
                        │                       │
                        │                 client.Jev.ask ──▶ cache? ──▶ wire.body ──▶ HTTP ──▶ wire.parse
                        │                       │
                        └── stop event ◀── halt (-m, -q, budget)
```

## The primitives (`jevcore/questions.py`)

`Noul`, `Choice` and `Score` are frozen dataclasses; `NoulAnswer`, `ChoiceAnswer` and
`ScoreAnswer` are their typed answers. Tools never see JSON. `ScoreAnswer.normalized` rescales any
rubric to 0..1 so a five-rung fit scale and a three-rung urgency scale compare.

## Wire formats (`jevcore/wire.py`)

Two dialects exist and only this module knows either:

| | System One (TypeSafe, OpenRouter, gateways) | Vercel AI Gateway (evaluation modality) |
|---|---|---|
| endpoint | `POST /v1/systemone` (OpenRouter: `/api/alpha/decisions`) | `POST /v4/ai/evaluation-model` |
| model | in the body | `ai-model-id` header |
| yes/no | `{"type":"noul"}` → `{"noul": p}` | `{"type":"boolean"}` → `{"probability": p}` |
| choice | `criteria: {name: desc}` → `choice`, `probabilities`, `confidence` | same, confidence under `providerMetadata.typesafe.confidence` |
| score | `criteria: [levels]` → `score`, `probabilities`, `legend`, `confidence` | same, no legend |
| usage | `input_tokens` (OpenRouter adds `cost`) | `inputTokens`, dollars under `providerMetadata.gateway.cost` |

Answers are validated strictly and rejected as `JevError` before anything is cached: a
probability outside 0..1, a choice that is not one of the options, a distribution carrying an
option that was never offered, a score outside the scale. Two things are deliberately tolerated
instead, because rejecting them would cost a usable answer rather than prevent a wrong one: a
token count that will not parse (metering, not a decision) and a probability keyed to a rung the
rubric does not have (the score itself is separately range-checked, so the unusable entry is
dropped and the answer stands).

## Backends and keys (`backends.py`, `auth.py`)

Resolution order: `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`, `AI_GATEWAY_API_KEY`, then
`JEV_GATEWAY_URL` + `JEV_GATEWAY_API_KEY`, then files in `~/.config/jev/`. `--api` or `JEV_API`
forces one; `JEV_URL` overrides the endpoint (tests point it at a local mock); `JEV_MODEL` sets the
model.

## The client (`client.py`)

- **Batching**: all questions about one state go in one request. Jev evaluates them in parallel,
  so extra questions cost tokens, not time. `jgrep -e a -e b` and `juniq` (up to 50 pair
  questions) rely on this.
- **Concurrency**: a semaphore bounds requests in flight (default 20). The pipeline also bounds
  how far the reader runs ahead, so `tail -f` never buffers.
- **Caches**: memory for the run; SQLite (`~/.cache/jev/answers.sqlite`, WAL) across runs and
  across the tools in one pipeline. Keyed on SHA-256(model, state, canonical question), where the
  state carries its kind, so text and the JSON object that spells it are different questions, and
  the model is the **version that answered**, not the alias that was asked for. `jev-latest` moves;
  a cache keyed on it would replay a retired version for ever. The first call of a run re-checks
  what the alias means today and the rest come from disk, or pin `--model` and none are needed.
  A cache file that is locked, full or written by another schema is dropped for the run, with one
  line on stderr; a decision is never lost to it.
- **In-flight sharing**: identical requests already in the air share one HTTP call. Logs repeat.
- **Retries**: transient statuses (408/409/425/429/5xx/529) and transport errors retry with jitter
  inside one total deadline (`--timeout`, default 15 s), so a dribbling response body cannot hang
  a line forever. The deadline starts when the request does, not when it joins the queue: waiting
  for a `-j` slot is not the backend being slow, and charging it to the request made a wide run
  time out against a healthy endpoint.
- **Rate-limit brake**: a 429/529 sets a client-wide "send nothing before T" timestamp
  (`Retry-After` when present, else 1, 2, 4, 8, 15 s) and switches the client into a trickle mode
  for two minutes, in which requests are sent one at a time. One rate limit does not fan out into
  twenty, and releasing the brake does not earn twenty more. Rate-limit waits do not count as retry
  attempts; only `--timeout` bounds them. When the backend asks for longer than `--timeout`, the
  request says so at once, naming the wait, instead of sleeping out its whole deadline and
  reporting that no attempt was made.
- **Budget**: `--budget` (default $1, `JEV_BUDGET`) stops a run that is about to cost more than
  you meant. A call's estimated cost is reserved when it starts rather than billed when it ends,
  because checking the spend alone let every request in flight clear the last dollar between them.
  With a budget set, one call runs alone first to establish the real price; it costs one round trip
  once per run. Cached answers stay free.
- **Fail-open**: `try_ask` returns `None` on a per-request error and reports it at most once a
  minute. Tools pass unjudged lines through and exit 5. `--strict` turns the first error into
  exit 4. `jgate` fails closed by default because a gate that opens during an outage is not a gate.

## Records and the pipeline (`inputs.py`, `pipeline.py`)

`iter_records` yields `Record`s from lines, paragraphs, whole files, JSONL fields or CSV columns,
as they arrive (it uses `readline`, which does not read ahead). `Pipeline.run` reads in a thread,
feeds a bounded queue, judges concurrently and delivers in input order. Order is the order records
arrived, not their `Record.seq`: a blank line, or a tool that filters before the pipeline, leaves a
hole in `seq`, and ordering on it stalled delivery at the hole and threw away everything judged
after it. `halt()` stops the reader and cancels outstanding requests. Tools whose nature needs the whole input (jsort, jpick, jhead,
juniq, jmatch) use `collect` and then `asyncio.gather` over the client, which still bounds
concurrency.

## How each tool asks (`rubric.py`)

| tool | primitive | state | question |
|---|---|---|---|
| jgrep, jgate, jwatch | noul | the record (jgrep `-C`: neighbours marked) | *The text fits this description: "…"* |
| jsort, jhead, jtag `--score` | score | the record | *Rate how well the text fits: "…"* on 5 rungs (or `--levels`) |
| jtag `--labels`, jroute | choice | the record | *Which label describes the text best?* with your labels |
| jpick | choice | `[{id, text}, …]` a group of candidates | *Choose the candidate that best fits "…"* |
| jmatch | choice | `{target, candidates: [{id, text}]}` | *Which candidate goes with the target…* plus `none` |
| juniq | noul × window | `{candidate, kept: […]}` | *candidate and kept[i] are duplicates in this sense: "…"* |

jpick and jmatch run tournaments: groups of `--group` (default 12) candidates, winners advance,
one final group gives the distribution. 1,000 lines cost about 90 calls.

## The mock (`mock.py`)

`MockJev` answers from keyword rules (a noul is 0.9 when a word of the quoted description, or a
listed synonym, appears in the state) and speaks both dialects, so the whole suite runs offline
in seconds. `serve()` puts it behind a real HTTP port for subprocess tests.
