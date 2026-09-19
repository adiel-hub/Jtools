# Contributing to j-tools

Thanks for helping. Two things matter more than anything else in this repository: the Hard Rule,
and the offline test suite.

## The Hard Rule (read twice)

j-tools is **the calibrated judgment layer for the shell**. Every tool is a pure
**judgment / ranking / classification / gating / routing** tool built on Jev's three primitives:

| primitive | question                         | answer                                   |
|-----------|----------------------------------|------------------------------------------|
| `noul`    | is this statement true?          | a probability from 0 to 1                |
| `choice`  | which option fits best?          | the option plus a distribution           |
| `score`   | where on this ordered scale?     | an interpolated position plus a distribution |

**Do not add tools that:**

- **generate text** (summaries, commit messages, shell commands, rewrites). That is what LLM
  wrappers such as `lx` do. Jev cannot write, and we would not want it to.
- **do vector or embedding similarity search**. That is what `grepai` / `ck` do.

If a proposed feature needs the model to *write* something, it does not belong here. If it
needs a vector index, it does not belong here. We judge, rank, classify, gate and route. That is
the niche, and it is the whole point.

Tools that were considered and rejected, so nobody re-adds them:

| proposal              | why not                                                       |
|-----------------------|---------------------------------------------------------------|
| `jfilter`             | it is `jgrep`                                                 |
| `jscore`, `jclassify` | both are `jtag` (`--score` / `--labels`)                      |
| `jsummarize`, `jcommit`, `jsh` | generation; out of scope by the Hard Rule            |
| `jsearch` (vectors)   | embeddings; out of scope by the Hard Rule                     |

A new tool must map to **one** primitive and do **one** decision job that no existing tool does.
Open a "Tool proposal" issue first; the template asks the right questions.

## Development

```bash
git clone https://github.com/adiel-hub/Jtools && cd Jtools
make install                         # uv sync --all-groups
make check                           # everything CI runs: lint, types, tests, docs, completions
```

`make help` lists the rest. Each target is one command from this file, so nothing in the Makefile
is a shortcut you cannot also type by hand:

```bash
uv sync --all-groups                 # or: python -m venv .venv && pip install -e . && pip install pytest pytest-asyncio pytest-timeout mypy ruff pillow
uv run pytest -q -m "not live"       # offline suite against MockJev; no key needed
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
```

The offline suite is the contract. Every tool is fully testable with no network: `MockJev`
(`src/jevcore/mock.py`) speaks both wire dialects and answers from predictable keyword rules, and
`serve()` runs it as a real localhost endpoint for subprocess tests (streaming, broken pipes,
dead endpoints).

The documentation is under test too: every `j*` command line printed in any Markdown file is
pulled out and handed to that tool's own parser, so a flag that is renamed or removed fails the
build instead of the reader.

With a key in the environment, `uv run pytest -q -m live` makes one real call per tool.

The tests exercise the code; one more check exercises the package. It installs the built wheel
into an empty virtualenv, where nothing from `src/` is importable, and drives all eleven commands
against a localhost mock, so a missing entry point or a module left out of the wheel fails here
rather than in someone's `pip install`:

```bash
make wheel      # uv build && uv run --no-project python scripts/check_wheel.py
```

### Layout

```
src/jevcore/        the library every tool shares (see docs/architecture.md)
  questions.py      the three primitives and their typed answers
  wire.py           System One and Vercel evaluation wire formats
  backends.py       TypeSafe / OpenRouter / Vercel / gateway definitions
  auth.py           key resolution
  client.py         async client: batching, concurrency, caches, retries, budget, meter
  cache.py          memory + SQLite answer caches
  inputs.py         records from lines, paragraphs, whole files, JSONL, CSV; discovery
  pipeline.py       concurrent judging with ordered delivery and backpressure
  rubric.py         how questions are phrased
  io.py             output, colour, redaction
  cli.py            shared flags, exit codes, dry run, stats
  mock.py           MockJev
src/jevtools/       one file per tool, plus jtools (list, doctor)
tests/              offline suite, subprocess tests, live smoke
bench/              reproducible benchmarks (live)
scripts/            asset, table and completion generators, plus the wheel check
completions/        bash and zsh completions, generated from the parsers
docs/               per-tool docs, architecture, benchmarks, recipes
```

### Adding a flag or a tool

1. Write the test first, against `MockJev`. If the mock cannot express the behaviour, extend the
   mock (it is small and rule-based on purpose).
2. Keep the shared surface shared: every tool takes the common flags from `jevcore.cli.add_common`
   and returns the common exit codes. Do not invent a tool-specific `--json` shape when
   `{"line", "score", ...}` will do.
3. Fail open unless the tool is a gate. An unjudged line passes through and the exit status
   becomes 5; nothing is silently dropped.
4. Phrase questions in `jevcore/rubric.py`, not inline. Jev answers the description the user
   wrote; keep our wrapping minimal and consistent.
5. Run the whole suite, `ruff`, `ruff format` and `mypy`. CI runs them on 3.11, 3.12 and 3.13.
6. Add a line to `CHANGELOG.md` and, for a new flag, to the tool's page in `docs/tools/`, then
   run `uv run python scripts/gen_completions.py` so the shell completions stay in step.

### Style

- Python 3.11+, type-annotated, `mypy --strict` clean. `ruff` decides formatting.
- Docstrings explain *why*; the code says *what*. A module docstring at the top of each tool is
  its man page.
- Errors go to stderr with the tool's name as prefix, once, not once per line.

### Releasing

Bump `version` in `pyproject.toml` and `src/jevcore/__init__.py`, add the CHANGELOG entry, tag
`vX.Y.Z` and push the tag. `.github/workflows/publish.yml` builds and publishes to PyPI via
Trusted Publishing.

On the **first** release, also restore the PyPI version badge at the top of `README.md` (it is
commented out beside the install badge that stands in for it) and drop the "once the first release
is on PyPI" note from the install block. Until then both would point at a project that does not
exist, and a badge reading "package or version not found" is the first thing a visitor sees.

## Code of conduct

Be kind, assume good faith, and keep reviews about the code. Maintainers may edit or remove
anything that is not.
