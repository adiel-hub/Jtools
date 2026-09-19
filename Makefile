# The checks CI runs, in one place. `make` on its own runs everything a push needs to survive.
#
# Every target is a thin wrapper around the command CONTRIBUTING.md documents, so there is nothing
# here you cannot also type by hand.

.DEFAULT_GOAL := check
.PHONY: check install test live lint types docs completions wheel assets clean

install:            ## create .venv and install everything, including the dev tools
	uv sync --all-groups

test:               ## the offline suite: no key, no network
	uv run pytest -q -m "not live" --timeout 180

live:               ## one real call per tool; needs a key in the environment
	uv run pytest -q -m live --timeout 300 -s

lint:               ## ruff, as a linter and as a formatter
	uv run ruff check src tests bench scripts
	uv run ruff format --check src tests bench scripts

types:              ## mypy --strict over the package
	uv run mypy src

docs:               ## the generated blocks in README.md and docs/benchmarks.md are current
	uv run python scripts/update_docs.py --check

completions:        ## completions/ matches the argparse parsers
	uv run python scripts/gen_completions.py --check

wheel:              ## build, then drive all eleven commands from an empty virtualenv
	uv build
	uv run --no-project python scripts/check_wheel.py

assets:             ## re-render the charts and demos from bench/results and docs/demo
	uv run python scripts/render_charts.py
	uv run python scripts/render_demo.py
	uv run python scripts/update_docs.py

check: lint types test docs completions   ## everything CI runs on a pull request

clean:
	rm -rf dist build .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

help:               ## list the targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | expand -t 16
