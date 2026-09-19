# Changelog

All notable changes to j-tools. The format follows [Keep a Changelog](https://keepachangelog.com);
versions follow [SemVer](https://semver.org).

## 0.1.0 - 2026-09-19

First public release: ten tools and the `jevcore` library.

### Tools

- `jgrep`: grep by meaning. `-e` (several descriptions in one call), `--all`, `-v`, `-o`, `-n`,
  `-H`, `-c`, `-l`, `-m`, `-q`, `-r` with `--glob`/`--exclude`/`--hidden`, `--para`, `--whole`,
  `-C N` context, `--jsonl` / `--csv` (the whole record, or one `--field` of it), `--json`,
  `--unordered`.
- `jsort`: rank lines by fit (score primitive). `--asc`, `-n`, `--with-score`, `--levels`.
- `jpick`: choose the best line by tournament (choice primitive). `--top`, `--why`, `--group`.
- `jgate`: exit status from a judgment. Whole input or `--each` / `--all`; `-P` pass-through;
  fails **closed** by default, `--fail-open` to override.
- `jwatch`: alerts on a live stream. `--exec`, `--cooldown`, `--max`, `--bell`, `--all-lines`.
- `juniq`: semantic dedup with a rolling `--window`; `-c`, `-d`, `-u`, `--show-groups`.
- `jhead`: the N most relevant lines in original order; `--tail`, `--reorder`.
- `jtag`: label column (`--labels` or a repeatable `--label NAME:DESC`, choice) or score column
  (`--score`, score) with `--sep`, `--suffix`, `--with-prob`, `--default`, `--scale`, `--levels`.
  A label description may contain commas.
- `jroute`: split a stream into bucket files; `--out-dir`, `--default`, `--stdout`, `--truncate`,
  `--no-files`, `-i`.
- `jmatch`: semantic join of two files; `--unmatched`, `--format`, `--group`, `--shortlist`.
- `jtools`: `list` and `doctor` (key check, a warning for a key file anyone on the machine can
  read, plus one real call with latency and cost).

### Library (`jevcore`)

- One async client for TypeSafe (`TYPESAFE_API_KEY`), OpenRouter (`OPENROUTER_API_KEY`), the
  Vercel AI Gateway (`AI_GATEWAY_API_KEY`) and any System One gateway
  (`JEV_GATEWAY_URL` + `JEV_GATEWAY_API_KEY`), with keys also read from `~/.config/jev/`.
- Both wire dialects: System One (`noul`/`choice`/`score`) and the Vercel evaluation modality
  (`boolean`/`choice`/`score`, confidence and cost from provider metadata).
- Questions about one state are batched into one request; identical in-flight requests share
  one call; answers are cached in memory and in `~/.cache/jev/answers.sqlite`.
- Bounded concurrency (default 20, `JEV_CONCURRENCY`), retries with jitter inside a total deadline
  (`--timeout`, `JEV_TIMEOUT`), a shared brake after HTTP 429/529 with `Retry-After` support and a
  trickle mode that sends one request at a time for two minutes after a rate limit, a dollar budget
  (default $1, `JEV_BUDGET`), and error reports throttled to one per minute.
- Common CLI surface on every tool: `--json`, `--dry-run`, `-p`, `--model`, `--api`, `-j`,
  `--timeout`, `--budget`, `--max-chars`, `--no-cache`, `--strict`, `--color`, `--stats`, plus
  `-q` (no end-of-run notes; errors still shown) and `-v` (one line per decision).
- Exit codes: 0 ok, 1 no match, 2 usage or unreadable file, 3 auth, 4 API/budget, 5 partial.

### Guarantees worth stating

These are the behaviours the test suite pins, because each one was wrong at some point during
development and the tests exist so it cannot go wrong again:

- `--budget` reserves a call's cost when the call starts, so requests in flight cannot all clear
  the last dollar between them.
- `--timeout` starts when a request starts, not when it joins the `-j` queue, so a healthy backend
  cannot time out because the run is wide.
- A rate limit the backend asks to outlast longer than `--timeout` is reported at once, naming the
  wait, instead of sleeping out the deadline and reporting nothing.
- Ordered output is ordered by arrival, so a blank line or an upstream filter cannot stall
  delivery and silently drop everything judged after it.
- Answers are cached under the model name the run asked for, so a rerun costs nothing, and each
  row records the version that produced it: when a moving alias like `jev-latest` comes to mean
  something else, the first real call to notice discards the old version's answers.
- A text state and the JSON object that spells it are different cache keys.
- `--jsonl` and `--csv` without `--field` send the record as a JSON object, which is what lets one
  description weigh several fields at once ("a production deploy outside working hours by a bot"
  is three fields). A record over `--max-chars` falls back to its first N characters of text,
  because an object has no first N characters and reporting a truncation while sending everything
  would be worse.
- Nothing a backend sends can make an answer name an option that was never offered or a rung
  outside its rubric.
- In `--json` mode every output line is a JSON object, blank input lines included.
- An unreadable input file is exit 2 in every tool, never "nothing matched", and a gate refuses
  to answer at all for input it could not read.
- `jwatch --exec` receives the alert line as an argument, never as command text, so a log line
  is data and not code. A spelling that would undo that is refused rather than accepted: an empty
  command, one that is nothing but the placeholder, a `{}` the user has quoted or escaped, and a
  command that ends inside an open quote. A command that names the line itself — `$1`, `${1}`,
  `$@`, `$*` — is left as written instead of being given a second copy of it.
- `jroute --truncate` empties nothing until a line is actually routed, so a run that reads
  nothing cannot destroy the previous run's buckets.
- Only a newline ends a line. A bare carriage return, which progress bars and some container logs
  produce constantly, stays inside its record.
- A byte-order mark, which every spreadsheet export begins with, does not become part of the
  first CSV column's name.
- Every tool asks through one code path, so `--strict` means the same thing in all of them.
- In `jgrep`, `-c` counts each file argument separately, as grep does, and a path-shaped pattern
  for `--glob` or `--exclude` is read relative to the directory being searched.
- Records that could not be judged pass through rather than disappear; only `jgate` fails closed.

### Repository

- Shell completions for every command in `completions/`, generated from the argparse parsers.
- Benchmarks in `bench/` that run the installed commands against a real endpoint and write
  `bench/results/*.json`; the README and `docs/benchmarks.md` numbers are generated from those
  files by `scripts/update_docs.py`, and the charts by `scripts/render_charts.py`.
- The README's terminal screenshots are recorded runs: `scripts/record_demos.py` saves each one as
  a cast in `docs/demo/` (asciinema v2 included) and `scripts/render_demo.py` renders the GIFs
  and SVGs.
- Offline test suite of 2,300+ tests against `MockJev`, including subprocess tests for streaming,
  broken pipes, dead endpoints, ordering under latency, bounded concurrency and a real shell
  pipeline; a matrix running every tool under every flag combination against ten awkward inputs;
  invariant tests that hold whatever Jev decides (a filter prints a subsequence of its input, `-v`
  is its exact complement, a sort is a permutation, every routed line lands in exactly one bucket);
  and documentation tests (links, tool pages, casts, generated blocks, every flag documented,
  every command line in the docs and every recorded demo parsed by its own tool). Live smoke
  tests run one real call per tool.
- CI on Python 3.11, 3.12 and 3.13: ruff, ruff format, mypy --strict, the offline suite, and the
  checks that the generated docs and completions are in step. A separate job builds the package
  and drives all eleven commands from an empty virtualenv with no key and no network, then runs
  the whole offline suite from the unpacked source distribution.
