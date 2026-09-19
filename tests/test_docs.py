"""The documentation is part of the product: it must not rot.

These tests need no key and no network. They check that every relative link and image in the
Markdown resolves, that every tool has a page and appears in the README, that the recorded
demo casts are valid asciinema v2, and that the generated benchmark blocks are in place.
"""

from __future__ import annotations

import json
import re
from html import unescape
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

    # And the whole of it, in order. Checking the opening lines was not enough: a render left over
    # from an older take of the same demo matched at the top and then drew an output line the
    # command had stopped producing -- a line that did appear elsewhere in the cast, so "is this
    # text somewhere in the recording" passed too. What has to hold is that the rows the SVG draws
    # ARE the rows the cast recorded, in sequence.
    score = re.compile(r"^(?:\d\.\d{3}|-)(?:\t|  )")  # drawn as its own element, so not in the row
    expected = "".join(
        (score.sub("", line.replace("\t", "  ")) if score.match(line.replace("\t", "  ")) else line.replace("\t", "  "))
        for _at, line in scene["stdout"]
    ) + "".join(scene["stderr"])
    drawn = "".join(
        unescape(text)
        for tag, text in re.findall(r'(<text[^>]*xml:space="preserve"[^>]*>)([^<]*)</text>', svg)
        if 'class="cmd"' not in tag  # the command line, drawn above the output
    )
    squeeze = re.compile(r"\s+")
    assert squeeze.sub(" ", drawn).strip() == squeeze.sub(" ", expected).strip(), (
        f"demo-{scene['name']}.svg does not draw what demo/{scene['name']}.json recorded. "
        "Re-render the demos (uv run python scripts/render_demo.py)."
    )


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


def test_every_inline_number_placeholder_is_one_the_generator_knows():
    """`<!--num:key-->…<!--/num-->` spans keep prose figures generated; a typo must not pass."""
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location("render_tables", ROOT / "scripts" / "render_tables.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    _sys.modules["render_tables"] = module
    spec.loader.exec_module(module)
    known = set(module.numbers())

    used = {key for md in markdown_files() for key in re.findall(r"<!--num:([a-z0-9_]+)-->", md.read_text())}
    assert used, "no inline number placeholders found; the prose has gone back to typed figures"
    assert used <= known, f"unknown placeholder(s): {sorted(used - known)}"
    for md in markdown_files():
        text = md.read_text()
        assert text.count("<!--num:") == text.count("<!--/num-->"), f"unbalanced number span in {md.name}"


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


def tool_specific_flags(tool: str) -> set[str]:
    """The long flags a tool adds beyond the ones every j-tool shares."""
    import argparse
    import importlib

    shared = argparse.ArgumentParser()
    from jevcore.cli import add_common

    add_common(shared)
    common = {flag for action in shared._actions for flag in action.option_strings}
    parser = importlib.import_module(f"jevtools.{tool}").parser()
    own = {flag for action in parser._actions for flag in action.option_strings}
    return {flag for flag in own - common if flag.startswith("--")}


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_every_flag_a_tool_adds_is_on_its_page(tool):
    """A flag nobody documented is a flag nobody can find. Shared flags live in the README."""
    page = (DOCS / "tools" / f"{tool}.md").read_text()
    missing = sorted(flag for flag in tool_specific_flags(tool) if flag not in page)
    assert not missing, f"docs/tools/{tool}.md does not mention {', '.join(missing)}"


def test_the_common_flags_are_listed_in_one_place():
    """Every shared flag appears in the README's common-options block, so no page repeats them."""
    import argparse

    from jevcore.cli import add_common

    shared = argparse.ArgumentParser()
    add_common(shared)
    readme = (ROOT / "README.md").read_text()
    block = readme.split("## Common options", 1)[1].split("Exit codes", 1)[0]
    skip = {"--help", "--version", "--threshold", "--concurrency", "--no-stats", "--verbose", "--quiet"}
    flags = {f for a in shared._actions for f in a.option_strings if f.startswith("--")} - skip
    missing = sorted(f for f in flags if f not in block)
    assert not missing, f"the README's common options do not list {', '.join(missing)}"


def chart_module():
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location("render_charts", ROOT / "scripts" / "render_charts.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    _sys.modules["render_charts"] = module
    spec.loader.exec_module(module)
    return module


def test_a_long_annotation_widens_the_chart_instead_of_being_clipped():
    """The bar and its label share the canvas, so the label's column has to be reserved for it.

    It was not: the space to the right of a bar was a fixed 120px, and "115 lines/s (5s for 600)"
    needs more, so the longest bar pushed its own label past the right edge, where it was simply
    cut off. Nothing failed -- an SVG with text outside its viewBox is still valid -- and the
    throughput chart shipped reading "(5s for 600". The geometry is checked here rather than the
    pixels: whatever the font does, the bar must end before the annotation column starts.
    """
    module = chart_module()
    rows = [("jgrep -j 1", 4.0, "#1f6feb", "4 lines/s (148s for 600)"), ("jgrep -j 32", 115.0, "#2da44e", "x" * 60)]
    svg = module.hbar_chart("t", "s", rows, "lines per second")
    canvas = int(re.search(r"width='(\d+)'", svg).group(1))
    for x, body in re.findall(r"<text x='([\d.]+)'(?![^>]*text-anchor='end')[^>]*>([^<]*)</text>", svg):
        assert float(x) < canvas, f"an annotation starts at {x} on a {canvas}px canvas: {body!r}"
    longest = max(float(x) for x, _ in re.findall(r"<rect x='(\d+)' y='[\d.]+' width='([\d.]+)'", svg) or [(0, 0)])
    assert longest < canvas, "a bar starts outside the canvas"
    # 60 characters cannot fit in the 120px the old layout reserved, so the canvas had to grow.
    assert canvas > 760, f"a 60-character annotation left the canvas at {canvas}px"
