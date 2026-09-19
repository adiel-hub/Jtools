"""Key resolution order, forcing, files and failure messages."""

import pytest

from jevcore.auth import available, resolve
from jevcore.backends import BACKENDS, OPENROUTER_URL, TYPESAFE_URL, VERCEL_URL, config_dir
from jevcore.errors import AuthError
from jevtools.jgrep import main as jgrep


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


@pytest.mark.parametrize(
    "api,env,dialect",
    [
        ("typesafe", {"TYPESAFE_API_KEY": "k"}, "noul"),
        ("openrouter", {"OPENROUTER_API_KEY": "k"}, "noul"),
        ("vercel", {"AI_GATEWAY_API_KEY": "vck_k"}, "boolean"),
        ("gateway", {"JEV_GATEWAY_API_KEY": "k", "JEV_GATEWAY_URL": "https://gw.example/v1/systemone"}, "noul"),
    ],
)
def test_a_tool_speaks_the_dialect_its_backend_uses(invoke, monkeypatch, api, env, dialect):
    """--api picks the backend, and with it the wire format. Nothing checked that end to end.

    The two dialects differ in the question type they send and in where the model is named: System
    One puts it in the body, the Vercel evaluation modality in a header.
    """
    for name in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "AI_GATEWAY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    res = invoke(jgrep, ["--api", api, "-p", "0", "a crash report"], "the app crashed on launch\n")
    assert res.code == 0, res.err
    body = res.mock.bodies[0]
    sent = body["questions"]["d0"]["type"]
    assert sent == dialect, f"{api} sent a {sent} question, not {dialect}"
    if dialect == "boolean":
        assert "ai-model-id" in {k.lower() for k in res.mock.headers[0]}, "the model was not named in a header"
        assert "model" not in body
    else:
        assert body.get("model"), "the model was not named in the body"
