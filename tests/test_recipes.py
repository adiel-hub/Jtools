"""The pipelines in docs/recipes.md, actually run.

`test_documented_commands.py` hands every documented command line to its own argparse parser, so
a flag that was renamed fails the build. That catches a command that cannot start; it cannot catch
a pipeline that no longer *composes* -- a column that moved, so the `cut -f1` after it takes the
wrong one; a tool that starts printing its scores on stderr, so the `sort -rn` after it sorts
nothing; an exit code that changed, so the `&&` after it stops firing.

So these run for real: a mock Jev on localhost, shims on PATH, a shell, and the recipe as written.
Sources a test machine does not have (`tail -f`, `journalctl`, `git diff`, `dmesg`) are replaced by
a file with the same shape; everything downstream of the first tool is the documented text.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

from jevcore.mock import MockJev, serve
from jevtools import TOOLS

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"

# The names a recipe reads that examples/ does not already ship. Everything else is copied from
# there, so the sample data the README hands people is what these pipelines actually run on.
INVENTED = {
    "leads.txt": "examples/feedback.txt",
    "reviews.txt": "examples/feedback.txt",
    "canary.log": "examples/app.log",
    "data.txt": "examples/feedback.txt",
    "kernel.log": "examples/app.log",
    "diff.txt": "examples/feedback.txt",
    "commits.txt": "examples/feedback.txt",
    "papers.csv": "id,abstract\n1,uses a natural experiment on wages\n2,a theory paper about wages\n",
    "events.jsonl": '{"message":"a payment failed for order 88213"}\n{"message":"checkout completed"}\n',
    "customers.csv": "customer,note\na,asked twice about cancelling\nb,renewed early and said thanks\n",
}

# (name, pipeline). Everything after the first tool is exactly what docs/recipes.md prints.
RECIPES = [
    ("angriest first, top ten, with scores", 'cat feedback.txt | jsort "angriest customer" -n 10 --with-score'),
    (
        "route an inbox into files",
        'cat inbox.txt | jroute "sales:a sales lead" "support:a support request" "spam:junk" -o sorted',
    ),
    ("work one bucket afterwards", 'touch sorted/sales.txt; jsort "highest buying intent" sorted/sales.txt | head -5'),
    (
        "label tickets and count the labels",
        'cat tickets.txt | jtag --labels "bug,feature,question,complaint" | cut -f1 | sort | uniq -c',
    ),
    (
        "collapse alerts per five minutes",
        'cat app.log | jwatch "something a human should look at right now" --cooldown 300 --bell',
    ),
    (
        "one notification per alert",
        """cat app.log | jwatch "a service is failing repeatedly" --exec 'echo notified {}'""",
    ),
    ("gate a deploy on a canary log", 'tail -50 canary.log | jgate "no errors that a customer would notice"'),
    ("the ten most relevant kernel lines", 'cat kernel.log | jhead 10 "a hardware error or a device that failed"'),
    (
        "merge only on a confident diff",
        'cat diff.txt | jgate -p 0.9 "a change that is safe to merge without review"',
    ),
    (
        "TODOs that are really bugs",
        """jgrep -r --glob '*.py' "a TODO or FIXME describing an actual bug, not a nicety" src/""",
    ),
    ("which commit describes the diff", 'cat commits.txt | jpick "the commit that introduced the retry logic" --why'),
    ("dedupe, biggest groups first", 'cat requests.txt | juniq "same underlying request" -c | sort -rn | head'),
    (
        "join invoices to payments",
        'jmatch invoices.txt payments.txt "the payment that settles this invoice" --unmatched',
    ),
    ("judge one CSV column", 'jgrep --csv --field abstract "uses a natural experiment" papers.csv > selected.csv'),
    ("judge one JSONL field", 'jgrep --jsonl --field message "a payment failed" events.jsonl'),
    (
        "rank, filter on the score, tag the survivors",
        'cat leads.txt | jsort "ready to buy" --with-score | '
        "awk -F'\\t' '$1 > 0.7 {print $2}' | jtag --labels \"enterprise,smb\"",
    ),
    (
        "score on an explicit scale",
        'cat reviews.txt | jtag --score "sentiment" --levels "furious,unhappy,neutral,happy,delighted" --suffix',
    ),
    ("see what would be sent", 'cat customers.csv | jsort "most likely to churn" --dry-run'),
    ("pin the model", 'cat data.txt | jsort "how urgent" --model jev-1.13.0'),
    ("a dollar budget", 'cat feedback.txt | jsort "most urgent" --budget 5'),
    ("more requests in flight", 'cat feedback.txt | jsort "most urgent" -j 32'),
    ("measure fresh, with stats", 'cat feedback.txt | jsort "most urgent" --no-cache --stats'),
    ("a longer per-request timeout", 'cat feedback.txt | jsort "most urgent" --timeout 30'),
    ("doctor", "jtools doctor"),
]

# What it means for a recipe to have broken: the shell could not find a tool, a tool rejected its
# arguments, or something crashed. An exit code of 1 is an answer ("nothing matched"), not a fault.
BROKEN = ("Traceback", "unrecognized arguments", "invalid choice", "command not found", "error:", "No such file")


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    """A directory with every file the recipes name, and shims so `jsort` is on PATH."""
    root = tmp_path_factory.mktemp("recipes")
    for sample in EXAMPLES.glob("*"):
        if sample.is_file() and sample.name != "README.md":
            shutil.copy(sample, root / sample.name)
    for name, source in INVENTED.items():
        text = (EXAMPLES / pathlib.Path(source).name).read_text() if source.startswith("examples/") else source
        (root / name).write_text(text)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("# TODO: this drops the last retry\nprint('hi')\n")
    shims = root / "bin"
    shims.mkdir()
    for tool in [*TOOLS, "jtools"]:
        shim = shims / tool
        shim.write_text(f'#!/bin/sh\nexec {sys.executable} -m jevtools.{tool} "$@"\n')
        shim.chmod(0o755)
    return root


@pytest.fixture(scope="module")
def recipe_env(workspace):
    server = serve(MockJev())
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("JEV_")}
    env.pop("vercel_api_key", None)
    env |= {
        "PATH": f"{workspace / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(pathlib.Path(__file__).resolve().parent.parent / "src"),
        "JEV_GATEWAY_URL": server.url,
        "JEV_GATEWAY_API_KEY": "test",
        "JEV_API": "gateway",
        "JEV_NO_CACHE": "1",
        "XDG_CACHE_HOME": str(workspace / ".cache"),
        "XDG_CONFIG_HOME": str(workspace / ".config"),
    }
    try:
        yield env
    finally:
        server.shutdown()


@pytest.mark.skipif(shutil.which("awk") is None or shutil.which("cut") is None, reason="needs coreutils and awk")
@pytest.mark.parametrize("name,pipeline", RECIPES, ids=[n for n, _ in RECIPES])
@pytest.mark.timeout(120)
def test_a_documented_pipeline_still_composes(workspace, recipe_env, name, pipeline):
    done = subprocess.run(
        pipeline, shell=True, cwd=workspace, env=recipe_env, capture_output=True, text=True, timeout=110
    )
    blame = next((word for word in BROKEN if word in done.stderr), None)
    assert blame is None, f"{name}: {blame} in stderr\n{done.stderr[-600:]}"
    assert done.returncode in (0, 1), f"{name}: exit {done.returncode}\n{done.stderr[-600:]}"


def test_every_pipeline_on_the_page_is_one_of_these():
    """A recipe added to the page without being added here would never be run.

    Matched on the description each pipeline quotes, which is the part that does not move when
    the page is reworded or the flags are reordered.
    """
    page = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "recipes.md").read_text()
    tools = {*TOOLS, "jtools"}
    documented = [
        line
        for raw in page.splitlines()
        if (line := raw.strip()) and not line.startswith(("#", "```"))
        if any(f"{tool} " in f" {line} " or line.startswith(f"{tool} ") for tool in tools)
    ]
    assert len(documented) >= 15, f"the extractor found only {len(documented)} pipelines; it has stopped working"
    covered = " ".join(pipeline for _, pipeline in RECIPES)
    missing = [
        line
        for line in documented
        if (quoted := re.findall(r'"([^"]{8,})"', line)) and not any(phrase in covered for phrase in quoted)
    ]
    assert not missing, "docs/recipes.md has pipelines nothing runs; add them to RECIPES:\n" + "\n".join(missing)
