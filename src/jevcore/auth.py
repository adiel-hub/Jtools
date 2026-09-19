"""Key resolution. First hit wins:

1. ``TYPESAFE_API_KEY``
2. ``OPENROUTER_API_KEY``
3. ``AI_GATEWAY_API_KEY`` (Vercel AI Gateway; ``VERCEL_AI_GATEWAY_API_KEY`` also works)
4. ``JEV_GATEWAY_URL`` + ``JEV_GATEWAY_API_KEY`` (a System One gateway of your own)
5. files under ``~/.config/jev/``: ``typesafe.key``, ``openrouter.key``, ``vercel.key``,
   ``gateway.key`` and ``gateway.url``

Force a backend with ``--api NAME`` or ``JEV_API=NAME``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .backends import BACKEND_NAMES, BACKENDS, Backend, config_dir
from .errors import AuthError


@dataclass(frozen=True, slots=True)
class Credentials:
    backend: Backend
    key: str
    url: str

    @property
    def redacted_key(self) -> str:
        k = self.key
        return k[:4] + "…" + k[-4:] if len(k) > 12 else "…"


def resolve(name: str | None = None) -> Credentials:
    """Pick a backend and its key. A name (or ``JEV_API``) wins; otherwise the first with a key."""
    name = (name or os.environ.get("JEV_API") or "").strip() or None
    if name:
        if name not in BACKENDS:
            raise AuthError(f"unknown API {name!r}; choose from {', '.join(BACKEND_NAMES)}")
        backend = BACKENDS[name]
        key = backend.key()
        if not key:
            raise AuthError(
                f"no key for {name}. Set {backend.key_env} or put the key in {backend.key_file}"
                + (f" (get one at {backend.console})" if backend.console else "")
            )
        url = backend.configured_url()
        if not url:
            raise AuthError(
                f"no URL for {name}. Set {backend.url_env} to the full System One endpoint "
                f"(for example https://gateway.example.com/v1/systemone) or put it in {backend.url_file}"
            )
        return Credentials(backend, key, url)
    for backend in BACKENDS.values():
        key = backend.key()
        url = backend.configured_url()
        if key and url:
            return Credentials(backend, key, url)
    envs = ", ".join(b.key_env for b in BACKENDS.values())
    # The most likely error anyone hits is their first one, and it was the only branch that said
    # what to set without saying where to get it. One backend, named concretely, beats four
    # consoles to choose between when you have none of them.
    start = BACKENDS["vercel"]
    raise AuthError(
        f"no API key. Set one of {envs}, or put a key in {config_dir()}/<api>.key\n"
        f"  no key yet? get one at {start.console}, then: export {start.key_env}=vck_..."
    )


def available() -> list[Backend]:
    """Every backend that has both a key and a URL right now, in priority order."""
    return [b for b in BACKENDS.values() if b.usable()]
