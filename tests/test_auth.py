"""Key resolution order, forcing, files and failure messages."""

import pytest

from jevcore.auth import available, resolve
from jevcore.backends import BACKENDS, OPENROUTER_URL, TYPESAFE_URL, VERCEL_URL, config_dir
from jevcore.errors import AuthError


def test_typesafe_wins_when_several_keys_exist(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vck_key")
    c = resolve()
    assert c.backend.name == "typesafe" and c.key == "test-key" and c.url == TYPESAFE_URL
    assert [b.name for b in available()] == ["typesafe", "openrouter", "vercel"]


def test_order_openrouter_then_vercel_then_gateway(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vck_key")
    assert resolve().url == OPENROUTER_URL
    monkeypatch.delenv("OPENROUTER_API_KEY")
    c = resolve()
    assert c.backend.name == "vercel" and c.url == VERCEL_URL and c.backend.model == "typesafe-ai/jev"
    monkeypatch.delenv("AI_GATEWAY_API_KEY")
    monkeypatch.setenv("JEV_GATEWAY_API_KEY", "gw")
    with pytest.raises(AuthError, match="no API key"):
        resolve()  # a gateway needs a URL too
    monkeypatch.setenv("JEV_GATEWAY_URL", "https://gw.example/v1/systemone")
    c = resolve()
    assert c.backend.name == "gateway" and c.url == "https://gw.example/v1/systemone"


def test_vercel_key_aliases(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    for name in ("VERCEL_AI_GATEWAY_API_KEY", "VERCEL_API_KEY", "vercel_api_key"):
        monkeypatch.setenv(name, "vck_x")
        assert resolve().backend.name == "vercel"
        monkeypatch.delenv(name)


def test_forcing_a_backend(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert resolve("openrouter").backend.name == "openrouter"
    monkeypatch.setenv("JEV_API", "openrouter")
    assert resolve().backend.name == "openrouter"
    with pytest.raises(AuthError, match="unknown API"):
        resolve("nope")
    with pytest.raises(AuthError, match="no key for vercel"):
        resolve("vercel")
    monkeypatch.setenv("JEV_GATEWAY_API_KEY", "gw")
    with pytest.raises(AuthError, match="no URL for gateway"):
        resolve("gateway")


def test_keys_and_urls_from_config_files(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    d = config_dir()
    d.mkdir(parents=True)
    (d / "gateway.key").write_text("file-key\n")
    (d / "gateway.url").write_text("https://files.example/v1/systemone\n")
    c = resolve()
    assert c.backend.name == "gateway" and c.key == "file-key" and c.url == "https://files.example/v1/systemone"
    (d / "typesafe.key").write_text("ts-file\n")
    assert resolve().backend.name == "typesafe"


def test_jev_url_overrides_any_backend(monkeypatch):
    monkeypatch.setenv("JEV_URL", "http://127.0.0.1:1/systemone")
    assert resolve().url == "http://127.0.0.1:1/systemone"


def test_no_key_message_lists_every_variable(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(AuthError) as e:
        resolve()
    for backend in BACKENDS.values():
        assert backend.key_env in str(e.value)


def test_blank_keys_do_not_count(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "   ")
    with pytest.raises(AuthError):
        resolve()


def test_redacted_key():
    from jevcore.auth import Credentials

    c = Credentials(BACKENDS["typesafe"], "sk-abcdefghijklmnop", TYPESAFE_URL)
    assert c.redacted_key == "sk-a…mnop"
    assert Credentials(BACKENDS["typesafe"], "short", TYPESAFE_URL).redacted_key == "…"
