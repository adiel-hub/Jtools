#!/usr/bin/env bash
# Everything CI runs, in CI's order, against the working tree. Run this before every commit:
# the suite passing locally is not the same as CI passing, and the difference has been the
# generated docs more than once.
set -u
cd "$(dirname "$0")/.."
fail=0
step() { printf '\n=== %s\n' "$1"; shift; "$@" || { echo "FAILED: $*"; fail=1; }; }
step "lint"        ruff check src tests bench scripts
step "format"      ruff format --check src tests bench scripts
step "types"       mypy src
step "tests"       python -m pytest -q -m "not live" --timeout 180 -p no:cacheprovider
step "docs"        python scripts/update_docs.py --check
step "completions" python scripts/gen_completions.py --check
[ "$fail" = 0 ] && echo && echo "all green" || { echo; echo "SOMETHING FAILED"; exit 1; }
