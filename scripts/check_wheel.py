"""Install the built wheel into an empty virtualenv and drive every command for real.

    uv build && uv run python scripts/check_wheel.py

Nothing from the source tree is importable in that virtualenv, so this is exactly what a user
gets from ``pip install jev-tools``: the console scripts, the packaged modules, and nothing
else.  The mock backend runs as a real System One endpoint on localhost, so the check needs no
API key and makes no network call.

The tests exercise the code; this exercises the package.  It is the one check that catches a
missing entry point, a module left out of the wheel, or an import that only works from a
checkout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = Path(tempfile.gettempdir()) / "jev-wheel-check"
SCRIPTS = ("jgrep", "jsort", "jpick", "jgate", "jwatch", "juniq", "jhead", "jtag", "jroute", "jmatch", "jtools")

TICKETS = "The app crashes on launch\nPlease add export to CSV\nHow do I reset my password?\n"
INBOX = "Can we get a quote for 50 seats?\nMy password reset link is broken\nFREE PRIZE click here\n"
REVIEWS = "I love this, it is great\nterrible, broke on day one\nit is fine I guess\n"

# Print the endpoint, then stay up until the parent kills us.
SERVER = "from jevcore.mock import serve\nimport threading\nprint(serve().url, flush=True)\nthreading.Event().wait()\n"


def newest_wheel() -> Path:
    wheels = sorted(ROOT.glob("dist/*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        sys.exit("no wheel in dist/; run `uv build` first")
    return wheels[-1]


def make_venv(wheel: Path) -> Path:
    if VENV.exists():
        shutil.rmtree(VENV)
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True, capture_output=True)
    pip = VENV / "bin" / "pip"
    done = subprocess.run([str(pip), "install", "-q", str(wheel)], capture_output=True, text=True, check=False)
    if done.returncode:
        sys.exit(f"installing {wheel.name} failed:\n{done.stderr[-2000:]}")
    return VENV / "bin"


def cases(work: Path) -> list[tuple[str, list[str], str, tuple[int, ...]]]:
    """One invocation per tool and per output mode, with the exit codes each may return."""
    a, b, log = work / "a.txt", work / "b.txt", work / "events.log"
    a.write_text("Jane Doe\nBob Stone\n")
    b.write_text("doe, jane\nstone, robert\n")
    log.write_text("all fine\nsomething went wrong here\n")
    return [
        ("jgrep", ["jgrep", "about passwords"], TICKETS, (0, 1)),
        ("jgrep --json", ["jgrep", "--json", "-p", "0", "about passwords"], TICKETS, (0, 1)),
        ("jgrep -c", ["jgrep", "-c", "a crash report"], TICKETS, (0, 1)),
        ("jgrep --dry-run", ["jgrep", "--dry-run", "anything"], TICKETS, (0,)),
        ("jsort", ["jsort", "how urgent"], TICKETS, (0,)),
        ("jsort --json", ["jsort", "--json", "how positive"], REVIEWS, (0,)),
        ("jhead", ["jhead", "2", "how urgent"], TICKETS, (0,)),
        ("jhead --tail", ["jhead", "2", "how urgent", "--tail", "-s"], TICKETS, (0,)),
        ("jpick", ["jpick", "the clearest bug report"], TICKETS, (0,)),
        ("jgate", ["jgate", "a support request"], "My password reset link is broken\n", (0, 1, 3)),
        ("jgate --each", ["jgate", "--each", "--fail-open", "a support request"], TICKETS, (0, 1, 3, 5)),
        ("juniq", ["juniq", "the same request"], TICKETS + "the app crashes when launched\n", (0,)),
        ("jtag --labels", ["jtag", "--labels", "bug,feature,question"], TICKETS, (0,)),
        ("jtag --score", ["jtag", "--score", "how positive (0-100)"], REVIEWS, (0,)),
        ("jtag --label", ["jtag", "--label", "bug:a defect, a crash", "--label", "ask:a question"], TICKETS, (0,)),
        ("jroute", ["jroute", "sales:a sales lead", "support:a support request", "-o", str(work / "r")], INBOX, (0,)),
        ("jmatch", ["jmatch", str(a), str(b), "the same person"], "", (0, 1)),
        ("jwatch", ["jwatch", "--max", "1", "an error", str(log)], "", (0, 1, 3, 5)),
        ("jtools", ["jtools"], "", (0,)),
        ("jtools list", ["jtools", "list"], "", (0,)),
        ("jtools version", ["jtools", "version"], "", (0,)),
        ("jtools doctor", ["jtools", "doctor"], "", (0,)),
    ]


def main() -> int:
    wheel = newest_wheel()
    bindir = make_venv(wheel)
    print(f"installed {wheel.name} into {VENV}")

    script = VENV / "mock_server.py"
    script.write_text(SERVER)
    server = subprocess.Popen(
        [str(bindir / "python"), str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert server.stdout is not None
    url = server.stdout.readline().strip()
    if not url.startswith("http"):
        server.kill()
        sys.exit(f"the mock server did not start: {url!r} {(server.stderr.read() if server.stderr else '')[-800:]}")
    print(f"mock endpoint: {url}")

    env = {k: v for k, v in os.environ.items() if not k.endswith(("API_KEY", "GATEWAY_URL"))}
    env |= {
        "PATH": f"{bindir}{os.pathsep}{env['PATH']}",
        "HOME": str(VENV),  # so no key file of this machine's is picked up
        "JEV_API": "gateway",
        "JEV_GATEWAY_URL": url,
        "JEV_GATEWAY_API_KEY": "wheel-check",
        "JEV_NO_CACHE": "1",
        "NO_COLOR": "1",
    }
    work = VENV / "work"
    work.mkdir(exist_ok=True)

    failures: list[str] = []
    try:
        for name, argv, stdin, ok in cases(work):
            done = subprocess.run(
                argv, input=stdin, capture_output=True, text=True, env=env, cwd=work, timeout=180, check=False
            )
            if done.returncode not in ok:
                failures.append(f"{name}: exit {done.returncode}\n{done.stderr[-800:]}")
            first = (done.stdout.splitlines() or [""])[0][:70]
            print(f"  {'ok ' if done.returncode in ok else 'FAIL'} {name:<20} exit={done.returncode} | {first}")

        for tool in SCRIPTS:
            for flag in ("--help", "--version"):
                done = subprocess.run([tool, flag], capture_output=True, text=True, env=env, timeout=60, check=False)
                if done.returncode or not done.stdout.strip():
                    failures.append(f"{tool} {flag}: exit {done.returncode} {done.stderr[-300:]}")
        print(f"  ok  --help/--version  {len(SCRIPTS)} commands")

        code = "import jevcore, jevtools, json; print(json.dumps(sorted(jevtools.TOOLS)))"
        done = subprocess.run(
            [str(bindir / "python"), "-c", code], capture_output=True, text=True, timeout=60, check=False
        )
        if done.returncode:
            failures.append(f"import jevcore/jevtools: {done.stderr[-500:]}")
        else:
            tools = json.loads(done.stdout)
            print(f"  ok  library import    {len(tools)} tools: {', '.join(tools)}")
    finally:
        server.terminate()

    print()
    if failures:
        print(f"{len(failures)} failure(s):")
        for failure in failures:
            print(" -", failure)
        return 1
    print("the wheel alone is enough: every command runs, every module imports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
