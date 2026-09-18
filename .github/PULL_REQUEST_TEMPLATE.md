## What

## Why

## Checks

- [ ] `uv run pytest -q -m "not live"` passes offline
- [ ] `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src` are clean
- [ ] New behaviour has a test against `MockJev` (and a live test if it changes the wire format)
- [ ] The tool still judges, ranks, classifies, gates or routes. It does not generate text.
- [ ] CHANGELOG.md has an entry
