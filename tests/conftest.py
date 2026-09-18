"""Shared fixtures. Every test runs offline against MockJev unless marked ``live``."""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx
import pytest

from jevcore.auth import Credentials
from jevcore.backends import BACKENDS, TYPESAFE_URL
from jevcore.mock import MockJev

SECRET_ENVS = (
    "TYPESAFE_API_KEY",
    "OPENROUTER_API_KEY",
    "AI_GATEWAY_API_KEY",
    "VERCEL_AI_GATEWAY_API_KEY",
    "VERCEL_API_KEY",
    "vercel_api_key",
    "JEV_GATEWAY_URL",
    "JEV_GATEWAY_API_KEY",
)
BEHAVIOUR_ENVS = ("JEV_API", "JEV_MODEL", "JEV_URL", "JEV_BUDGET", "JEV_NO_CACHE", "JEV_PRICE_PER_MTOK", "NO_COLOR")


@pytest.fixture(autouse=True)
def isolated_env(request, monkeypatch, tmp_path):
    """A clean environment with one fake TypeSafe key and throwaway config/cache dirs.

    Live tests keep the real environment so they can find a real key.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("COLUMNS", "120")
    if request.node.get_closest_marker("live"):
        return
    for name in SECRET_ENVS + BEHAVIOUR_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")


@pytest.fixture
def mock() -> MockJev:
    return MockJev()


@pytest.fixture
def transport(mock: MockJev) -> httpx.MockTransport:
    return httpx.MockTransport(mock)


@pytest.fixture
def creds() -> Credentials:
    return Credentials(BACKENDS["typesafe"], "test-key", TYPESAFE_URL)


@dataclass
class Result:
    code: int
    out: str
    err: str
    mock: MockJev

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()


Invoke = Callable[..., Result]


@pytest.fixture
def invoke(monkeypatch, mock: MockJev) -> Invoke:
    """``invoke(main, argv, stdin="...")`` runs a tool in-process against the mock."""

    def _invoke(main, argv: Sequence[str], stdin: str = "", *, mock_override: MockJev | None = None) -> Result:
        m = mock_override or mock
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), transport=httpx.MockTransport(m), out=out, err=err)
        return Result(code, out.getvalue(), err.getvalue(), m)

    return _invoke


def write(tmp_path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)
