"""Every tool, under many flag combinations, on awkward input: no traceback, always a known exit.

The per-tool tests check what each flag does. This one checks that nothing a user can type makes a
tool fall over. It is deliberately broad and shallow: for every tool, the cross product of the
output modes and the behaviour flags, against ten kinds of input that have each broken something
somewhere at least once. Roughly nineteen hundred runs, about two minutes, and the only claims are
the ones that must hold everywhere: a known exit code, no traceback, and valid JSON in --json.
"""

from __future__ import annotations

import itertools
import json

import pytest

from jevcore.mock import POISON, MockJev
from jevtools.jgate import main as jgate
from jevtools.jgrep import main as jgrep
from jevtools.jhead import main as jhead
from jevtools.jmatch import main as jmatch
from jevtools.jpick import main as jpick
from jevtools.jroute import main as jroute
from jevtools.jsort import main as jsort
from jevtools.jtag import main as jtag
from jevtools.juniq import main as juniq
from jevtools.jwatch import main as jwatch

EXIT_CODES = {0, 1, 2, 3, 4, 5}

INPUTS = {
    "ordinary": "The app crashes on launch\nPlease add export to CSV\nHow do I reset my password?\n",
    "empty": "",
    "one blank line": "\n",
    "blanks between lines": "crash one\n\n\ncrash two\n",
    "no trailing newline": "crash one\ncrash two",
    "crlf": "crash one\r\ncrash two\r\n",
    "unicode and separators": "naïve café 日本語\ta tab\tand a | pipe\n-7\t0x1f\t1e9\n",
    "one unjudgeable line": f"crash one\n{POISON} nobody can judge this\ncrash two\n",
    "a very long line": "x" * 5000 + " crash\n",
    "looks like a flag": "--not-a-flag\n-v\n",
}

# (tool, the arguments that make it do its job); files come from stdin unless a tool needs paths.
TOOLS = [
    ("jgrep", jgrep, ["a crash report"]),
    ("jsort", jsort, ["how urgent"]),
    ("jhead", jhead, ["2", "how urgent"]),
    ("jpick", jpick, ["the clearest bug report"]),
    ("jgate", jgate, ["a support request"]),
    ("jwatch", jwatch, ["something worth a look", "--max", "2"]),
    ("juniq", juniq, ["the same underlying request"]),
    ("jtag", jtag, ["--labels", "bug,feature,question"]),
    ("jroute", jroute, ["sales:a sales lead", "support:a support request"]),
]

MODES = [[], ["--json"], ["--dry-run"]]
BEHAVIOURS = [[], ["-q"], ["--verbose"], ["--strict"], ["-p", "0.9"], ["-j", "1"], ["--no-cache"]]


def cases() -> list[tuple[str, list[str], str, str]]:
    out = []
    for (name, _, base), mode, behaviour in itertools.product(TOOLS, MODES, BEHAVIOURS):
        for input_name, text in INPUTS.items():
            out.append((name, [*base, *mode, *behaviour], input_name, text))
    return out


CASES = cases()
BY_NAME = {name: fn for name, fn, _ in TOOLS}


@pytest.mark.parametrize(
    "tool,argv,input_name,text",
    CASES,
    ids=[f"{t}-{'_'.join(a[1:]) or 'plain'}-{i}" for t, a, i, _ in CASES],
)
def test_no_combination_crashes(invoke, tmp_path, monkeypatch, tool, argv, input_name, text):
    monkeypatch.chdir(tmp_path)  # jroute writes bucket files into the working directory
    main = BY_NAME[tool]
    res = invoke(main, argv, text)
    assert res.code in EXIT_CODES, f"{tool} {argv} on {input_name!r} exited {res.code}"
    assert "Traceback" not in res.err, res.err[-800:]
    if "--json" in argv and "--dry-run" not in argv:
        for line in res.lines:
            json.loads(line)  # every line of --json output is an object


def test_jmatch_survives_the_same_inputs(invoke, tmp_path, monkeypatch):
    """jmatch takes two file arguments rather than stdin, so it gets its own pass."""
    monkeypatch.chdir(tmp_path)
    for name, text in INPUTS.items():
        a, b = tmp_path / "a.txt", tmp_path / "b.txt"
        a.write_text(text, encoding="utf-8")
        b.write_text(INPUTS["ordinary"], encoding="utf-8")
        for extra in ([], ["--json"], ["--unmatched"], ["-q"], ["--verbose"], ["--shortlist", "1"]):
            res = invoke(jmatch, [str(a), str(b), "the same request", *extra])
            assert res.code in EXIT_CODES, f"jmatch {extra} on {name!r} exited {res.code}"
            assert "Traceback" not in res.err, res.err[-800:]


def test_a_dead_backend_never_produces_a_traceback(invoke, tmp_path, monkeypatch):
    """Every tool, every output mode, against a backend that answers nothing but 500s."""
    monkeypatch.chdir(tmp_path)
    dead = MockJev(script=[500] * 200)
    for name, main, base in TOOLS:
        for extra in ([], ["--json"], ["--strict"], ["-q"]):
            res = invoke(main, [*base, *extra], INPUTS["ordinary"], mock_override=dead)
            assert res.code in EXIT_CODES, f"{name} {extra} exited {res.code}"
            assert "Traceback" not in res.err, res.err[-800:]
