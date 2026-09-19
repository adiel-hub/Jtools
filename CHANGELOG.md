# Changelog

All notable changes to j-tools. The format follows [Keep a Changelog](https://keepachangelog.com);
versions follow [SemVer](https://semver.org).

## 0.1.0 - 2026-09-19

First public release: ten tools and the `jevcore` library.

### Tools

- `jgrep`: grep by meaning. `-e` (several descriptions in one call), `--all`, `-v`, `-o`, `-n`,
  `-H`, `-c`, `-l`, `-m`, `-q`, `-r` with `--glob`/`--exclude`/`--hidden`, `--para`, `--whole`,
  `-C N` context, `--jsonl --field`, `--csv --field`, `--json`, `--unordered`.
- `jsort`: rank lines by fit (score primitive). `--asc`, `-n`, `--with-score`, `--levels`.
- `jpick`: choose the best line by tournament (choice primitive). `--top`, `--why`, `--group`.
- `jgate`: exit status from a judgment. Whole input or `--each` / `--all`; `-P` pass-through;
  fails **closed** by default, `--fail-open` to override.
- `jwatch`: alerts on a live stream. `--exec`, `--cooldown`, `--max`, `--bell`, `--all-lines`.
- `juniq`: semantic dedup with a rolling `--window`; `-c`, `-d`, `-u`, `--show-groups`.
- `jhead`: the N most relevant lines in original order; `--tail`, `--reorder`.
- `jtag`: label column (`--labels`, choice) or score column (`--score`, score) with `--sep`,
  `--suffix`, `--with-prob`, `--default`, `--scale`, `--levels`.
- `jroute`: split a stream into bucket files; `--out-dir`, `--default`, `--stdout`, `--truncate`,
  `--no-files`, `-i`.
- `jmatch`: semantic join of two files; `--unmatched`, `--format`, `--group`, `--shortlist`.
- `jtools`: `list` and `doctor` (key check plus one real call with latency and cost).

### Library (`jevcore`)

- One async client for TypeSafe (`TYPESAFE_API_KEY`), OpenRouter (`OPENROUTER_API_KEY`), the
  Vercel AI Gateway (`AI_GATEWAY_API_KEY`) and any System One gateway
  (`JEV_GATEWAY_URL` + `JEV_GATEWAY_API_KEY`), with keys also read from `~/.config/jev/`.
- Both wire dialects: System One (`noul`/`choice`/`score`) and the Vercel evaluation modality
  (`boolean`/`choice`/`score`, confidence and cost from provider metadata).
- Questions about one state are batched into one request; identical in-flight requests share
  one call; answers are cached in memory and in `~/.cache/jev/answers.sqlite`.
- Bounded concurrency (default 20), retries with jitter inside a total deadline, a shared brake
  after HTTP 429/529 with `Retry-After` support, a dollar budget (default $1), and error
  reports throttled to one per minute.
- Common CLI surface on every tool: `--json`, `--dry-run`, `-p`, `--model`, `--api`, `-j`,
  `--timeout`, `--budget`, `--max-chars`, `--no-cache`, `--strict`, `--color`, `--stats`.
- Exit codes: 0 ok, 1 no match, 2 usage, 3 auth, 4 API/budget, 5 partial (some lines unjudged).
