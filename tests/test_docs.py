"""The documentation is part of the product: it must not rot.

These tests need no key and no network. They check that every relative link and image in the
Markdown resolves, that every tool has a page and appears in the README, that the recorded
demo casts are valid asciinema v2, and that the generated benchmark blocks are in place.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jevtools import TOOLS

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
LINK = re.compile(r'!\[[^\]]*\]\(([^)\s]+)\)|\]\(([^)\s#]+)\)|<img[^>]+src="([^"]+)"')


def markdown_files() -> list[Path]:
    return [p for p in sorted(ROOT.rglob("*.md")) if ".venv" not in p.parts and "out" not in p.parts]


def test_every_relative_link_and_image_resolves():
    missing = []
    for md in markdown_files():
        for match in LINK.finditer(md.read_text()):
            target = next(g for g in match.groups() if g)
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (md.parent / target.split("#")[0]).resolve().exists():
                missing.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not missing, "broken relative links: " + "; ".join(missing)


def test_every_tool_has_a_page_and_a_readme_row():
    readme = (ROOT / "README.md").read_text()
    for tool in TOOLS:
        page = DOCS / "tools" / f"{tool}.md"
        assert page.exists(), f"{tool} has no docs page"
        assert f"[`{tool}`](docs/tools/{tool}.md)" in readme, f"{tool} is not in the README table"
        assert tool in page.read_text()[:200], f"{page} does not start by naming {tool}"
    assert "[`jtools`](docs/tools/jtools.md)" in readme


def test_tool_pages_state_their_primitive():
    for tool in TOOLS:
        text = (DOCS / "tools" / f"{tool}.md").read_text().lower()
        assert any(word in text for word in ("noul", "choice", "score")), f"{tool}.md names no primitive"


@pytest.mark.parametrize("cast", sorted((DOCS / "demo").glob("*.cast")), ids=lambda p: p.name)
def test_recorded_casts_are_valid_asciinema_v2(cast: Path):
    lines = cast.read_text().splitlines()
    header = json.loads(lines[0])
    assert header["version"] == 2 and header["width"] > 0 and header["height"] > 0
    events = [json.loads(line) for line in lines[1:] if line.strip()]
    assert events, "a cast with no output is not a demo"
    assert all(len(e) == 3 and isinstance(e[0], int | float) and e[1] == "o" for e in events)
    assert events == sorted(events, key=lambda e: e[0]), "events are not in time order"


@pytest.mark.parametrize("cast", sorted((DOCS / "demo").glob("*.json")), ids=lambda p: p.name)
def test_every_recorded_cast_has_rendered_assets(cast: Path):
    scene = json.loads(cast.read_text())
    for suffix in (".gif", ".svg"):
        asset = DOCS / "assets" / f"demo-{scene['name']}{suffix}"
        # A one-line demo makes a small SVG; anything under a few hundred bytes is a broken render.
        assert asset.exists() and asset.stat().st_size > 400, f"{asset.name} is missing or empty"
    svg = (DOCS / "assets" / f"demo-{scene['name']}.svg").read_text()
    for _at, line in scene["stdout"][:3]:
        # The renderer draws a leading score column as its own element, so compare the text after it.
        body = line.split("\t", 1)[-1].replace("\t", "  ").strip()
        head = body[:24]
        if head and not any(c in head for c in "<>&"):
            assert head in svg, f"demo-{scene['name']}.svg does not show the recorded line {head!r}"


def test_generated_benchmark_blocks_are_present():
    for path, name in ((ROOT / "README.md", "bench-summary"), (DOCS / "benchmarks.md", "bench-tables")):
        text = path.read_text()
        assert f"<!-- BEGIN {name}" in text and f"<!-- END {name} -->" in text
        assert text.index(f"<!-- BEGIN {name}") < text.index(f"<!-- END {name} -->")


@pytest.mark.parametrize(
    "labels",
    [
        ["world:world affairs", "sports:sport", "business:markets, money", "scitech:science"],
        "world:world affairs,sports:sport,business:markets,scitech:science",
    ],
    ids=["one --label per class", "a single --labels string"],
)
def test_the_news_table_is_whole_for_either_label_shape(labels, monkeypatch):
    """A run recorded either way must still produce a heading, the accuracy line and the table."""
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location("render_tables", ROOT / "scripts" / "render_tables.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    _sys.modules["render_tables"] = module
    spec.loader.exec_module(module)

    result = {
        "dataset": "AG News test split",
        "lines": 80,
        "judged": 78,
        "labels": labels,
        "accuracy": 0.83,
        "macro_f1": 0.81,
        "per_class": {"world": {"precision": 0.8, "recall": 0.9, "f1": 0.85}},
        "jtag_run": {"seconds": 12.5, "tokens": 1000},
    }
    monkeypatch.setattr(module, "load", lambda name: result if name == "accuracy-news" else None)
    table = module.accuracy_table()
    assert "### jtag four-way classification: AG News test split (78 of 80 sampled articles judged)" in table
    assert "Accuracy **0.83**, macro F1 0.81" in table
    assert "| label | precision | recall | F1 |" in table
    assert "| world | 0.80 | 0.90 | 0.85 |" in table
    assert "world affairs" in table


def test_contributing_states_the_hard_rule():
    text = (ROOT / "CONTRIBUTING.md").read_text()
    assert "Hard Rule" in text
    for forbidden in ("generate text", "embedding"):
        assert forbidden in text, f"CONTRIBUTING.md does not rule out {forbidden}"


def test_shell_completions_match_the_parsers():
    """`scripts/gen_completions.py --check`: a new flag must be completable."""
    import subprocess
    import sys as _sys

    proc = subprocess.run(
        [_sys.executable, str(ROOT / "scripts" / "gen_completions.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
