"""The installed commands as real processes: streaming through a pipe, fail-open against a dead
endpoint, every console script answering --version, and jtools doctor against the mock server."""

import os
import select
import shutil
import subprocess
import sys
import time

import pytest

from jevcore.mock import MockJev, serve
from jevtools import TOOLS

DEAD_URL = "http://127.0.0.1:9/v1/systemone"  # discard port: connection refused


def env_for(url: str, tmp_path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("JEV_")}
    env.pop("vercel_api_key", None)
    env.update(
        {
            "JEV_GATEWAY_URL": url,
            "JEV_GATEWAY_API_KEY": "test",
            "JEV_API": "gateway",
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def module_cmd(tool: str) -> list[str]:
    return [sys.executable, "-m", f"jevtools.{tool}"]


@pytest.mark.timeout(30)
def test_jwatch_streams_without_waiting_for_eof(tmp_path):
    server = serve(MockJev())
    try:
        proc = subprocess.Popen(
            module_cmd("jwatch") + ["an error", "--no-cache"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env_for(server.url, tmp_path),
        )
        assert proc.stdin and proc.stdout
        proc.stdin.write("INFO fine\nERROR disk failed\n")
        proc.stdin.flush()
        ready, _, _ = select.select([proc.stdout], [], [], 15)
        assert ready, "no output arrived while stdin was still open: the tool buffers a live pipe"
        assert proc.stdout.readline() == "ERROR disk failed\n"
        proc.stdin.write("ERROR second\n")
        proc.stdin.flush()
        assert proc.stdout.readline() == "ERROR second\n"
        proc.stdin.close()
        assert proc.wait(timeout=15) == 0
        assert proc.stdout.read() == ""
    finally:
        proc.kill()
        server.shutdown()


@pytest.mark.timeout(30)
def test_jgrep_streams_and_preserves_order_under_latency(tmp_path):
    server = serve(MockJev(), latency=0.02)
    try:
        lines = "".join(f"alpha {i}\n" if i % 2 else f"beta {i}\n" for i in range(40))
        proc = subprocess.run(
            module_cmd("jgrep") + ["alpha", "-j", "8", "--no-cache"],
            input=lines,
            text=True,
            capture_output=True,
            env=env_for(server.url, tmp_path),
            timeout=25,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.splitlines() == [f"alpha {i}" for i in range(40) if i % 2]
    finally:
        server.shutdown()


@pytest.mark.timeout(60)
def test_dead_endpoint_fails_open_then_strict_fails_closed(tmp_path):
    env = env_for(DEAD_URL, tmp_path)
    t0 = time.perf_counter()
    proc = subprocess.run(
        module_cmd("jgrep") + ["alpha", "--timeout", "1", "-j", "4", "--no-cache"],
        input="one\ntwo\nthree\n",
        text=True,
        capture_output=True,
        env=env,
        timeout=50,
    )
    assert proc.returncode == 5
    assert proc.stdout == "one\ntwo\nthree\n"  # every line passed through unjudged
    assert proc.stderr.count("gave up") == 1  # one report, not one per line
    assert time.perf_counter() - t0 < 30
    proc = subprocess.run(
        module_cmd("jgrep") + ["alpha", "--timeout", "1", "--strict", "--no-cache"],
        input="one\ntwo\n",
        text=True,
        capture_output=True,
        env=env,
        timeout=50,
    )
    assert proc.returncode == 4 and proc.stdout == ""
    proc = subprocess.run(
        module_cmd("jgate") + ["safe", "--timeout", "1", "--no-cache"],
        input="one\n",
        text=True,
        capture_output=True,
        env=env,
        timeout=50,
    )
    assert proc.returncode == 4  # a gate never opens on an outage


@pytest.mark.timeout(60)
def test_every_console_script_is_installed_and_answers_version():
    for tool in [*TOOLS, "jtools"]:
        exe = shutil.which(tool) or os.path.join(os.path.dirname(sys.executable), tool)
        if not os.path.exists(exe):
            pytest.skip(f"{tool} is not installed on PATH (run `uv pip install -e .`)")
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0 and tool in proc.stdout, (tool, proc.stdout, proc.stderr)


@pytest.mark.timeout(30)
def test_jtools_doctor_and_list(tmp_path):
    server = serve(MockJev())
    try:
        proc = subprocess.run(
            module_cmd("jtools") + ["doctor"],
            capture_output=True,
            text=True,
            env=env_for(server.url, tmp_path),
            timeout=25,
        )
        assert proc.returncode == 0 and "ok: one call in" in proc.stdout and "gateway" in proc.stdout
        proc = subprocess.run(module_cmd("jtools") + ["list"], capture_output=True, text=True, timeout=25)
        assert proc.returncode == 0 and all(tool in proc.stdout for tool in TOOLS)
        proc = subprocess.run(
            module_cmd("jtools") + ["doctor"],
            capture_output=True,
            text=True,
            env=env_for(DEAD_URL, tmp_path),
            timeout=25,
        )
        assert proc.returncode == 4
    finally:
        server.shutdown()


@pytest.mark.timeout(30)
def test_broken_pipe_is_quiet(tmp_path):
    server = serve(MockJev())
    try:
        # `jgrep ... | head -1` closes our stdout early; the tool must exit cleanly, no traceback.
        cmd = f"{sys.executable} -m jevtools.jgrep alpha --no-cache | head -1"
        proc = subprocess.run(
            cmd,
            shell=True,
            input="alpha\n" * 500,
            text=True,
            capture_output=True,
            env=env_for(server.url, tmp_path),
            timeout=25,
        )
        assert proc.stdout == "alpha\n" and "Traceback" not in proc.stderr
    finally:
        server.shutdown()
