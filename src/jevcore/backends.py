"""Where Jev lives: the APIs that serve it, their default models and how each one is keyed.

Order matters. With keys for several backends, the first one in :data:`BACKENDS` wins unless the
user forces one with ``--api`` or ``JEV_API``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .wire import SYSTEMONE, VERCEL, Wire

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_URL = "https://openrouter.ai/api/alpha/decisions"
VERCEL_URL = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"

DEFAULT_MODEL_ALIAS = "jev-latest"


def config_dir() -> Path:
    """``~/.config/jev`` (or ``$XDG_CONFIG_HOME/jev``). Shared with jgrep-compatible tools."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "jev"


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "jev"


@dataclass(frozen=True)
class Backend:
    name: str
    """Short name used by ``--api`` and ``JEV_API``."""
    url: str
    """The full endpoint, or empty for a gateway whose URL the user supplies."""
    model: str
    """Default model ID on this backend."""
    key_envs: tuple[str, ...]
    """Environment variables that may hold the key, first hit wins."""
    wire: Wire = field(default=SYSTEMONE, compare=False)
    url_env: str | None = None
    """For a backend without a fixed URL: the variable that names it."""
    console: str = ""
    """Where a human gets a key."""

    @property
    def key_env(self) -> str:
        return self.key_envs[0]

    @property
    def key_file(self) -> Path:
        return config_dir() / f"{self.name}.key"

    @property
    def url_file(self) -> Path:
        return config_dir() / f"{self.name}.url"

    def key(self) -> str | None:
        for env in self.key_envs:
            value = os.environ.get(env)
            if value and value.strip():
                return value.strip()
        try:
            text = self.key_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return text or None

    def configured_url(self) -> str | None:
        """The endpoint to call. ``JEV_URL`` overrides everything, for tests and proxies."""
        override = os.environ.get("JEV_URL")
        if override and override.strip():
            return override.strip()
        if self.url:
            return self.url
        if self.url_env:
            value = os.environ.get(self.url_env)
            if value and value.strip():
                return value.strip()
        try:
            text = self.url_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return text or None

    def usable(self) -> bool:
        return self.key() is not None and self.configured_url() is not None


BACKENDS: dict[str, Backend] = {
    "typesafe": Backend(
        "typesafe",
        TYPESAFE_URL,
        DEFAULT_MODEL_ALIAS,
        ("TYPESAFE_API_KEY",),
        console="https://console.typesafe.ai/settings/keys",
    ),
    "openrouter": Backend(
        "openrouter",
        OPENROUTER_URL,
        "~typesafe/jev-latest",
        ("OPENROUTER_API_KEY",),
        console="https://openrouter.ai/keys",
    ),
    "vercel": Backend(
        "vercel",
        VERCEL_URL,
        "typesafe-ai/jev",
        ("AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_API_KEY", "VERCEL_API_KEY", "vercel_api_key"),
        wire=VERCEL,
        console="https://vercel.com/ai-gateway",
    ),
    # Anything that speaks System One and takes its own key: LiteLLM, a corporate proxy, a mock.
    "gateway": Backend(
        "gateway",
        "",
        DEFAULT_MODEL_ALIAS,
        ("JEV_GATEWAY_API_KEY",),
        url_env="JEV_GATEWAY_URL",
    ),
}

BACKEND_NAMES = tuple(BACKENDS)
