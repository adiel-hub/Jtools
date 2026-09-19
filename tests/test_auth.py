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


def test_the_no_key_error_says_where_to_get_one(monkeypatch):
    """The first error a new user hits is the one that has to be actionable.

    It named the four variables to set and the file to write, and never said where a key comes
    from -- which is the one thing somebody with no key does not have. Naming Vercel concretely
    beats four consoles to choose between.
    """
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(AuthError) as e:
        resolve()
    message = str(e.value)
    assert "https://vercel.com/ai-gateway" in message, "nowhere to get a key"
    assert "export AI_GATEWAY_API_KEY=" in message, "no command to copy"
    # Still says everything it said before: a reader may already hold one of the other keys.
    for env in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "AI_GATEWAY_API_KEY", "JEV_GATEWAY_API_KEY"):
        assert env in message
    assert "<api>.key" in message, "the file path is the other way to set it"


def test_naming_a_backend_with_no_key_points_at_that_one(monkeypatch):
    """--api openrouter is not a new user; the hint follows what they asked for."""
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(AuthError) as e:
        resolve("openrouter")
    assert "https://openrouter.ai/keys" in str(e.value)


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


def test_doctor_names_a_key_file_anyone_on_the_machine_can_read(tmp_path, monkeypatch):
    """`~/.config/jev/*.key` is the tidy alternative this tool recommends; it should also say
    when the file it recommended is world-readable."""
    import io
    import os

    import httpx

    from jevcore.mock import MockJev
    from jevtools.jtools import main

    for name in list(os.environ):
        if name.endswith("API_KEY"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("vercel_api_key", raising=False)
    cfg = tmp_path / "jev"
    cfg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (cfg / "typesafe.key").write_text("sk-abcdefghijklmnop\n")
    (cfg / "openrouter.key").write_text("sk-or-abcdefghijklmnop\n")
    os.chmod(cfg / "typesafe.key", 0o644)
    os.chmod(cfg / "openrouter.key", 0o600)

    out = io.StringIO()
    code = main(["doctor"], transport=httpx.MockTransport(MockJev()), out=out, err=io.StringIO())
    text = out.getvalue()
    assert code == 0
    assert "typesafe.key is readable by others" in text
    assert "openrouter.key" not in text, "a key file at 0600 was reported as loose"
    assert "sk-abcdefghijklmnop" not in text, "doctor printed a key in full"


def test_doctor_prices_a_call_the_gateway_billed_at_zero(monkeypatch, tmp_path):
    """A plan that does not bill per call reports $0, which is not the same as costing nothing.

    Shown as "$0.0000000" it read as free, and disagreed with what --stats printed for the very
    same call: the meter has always fallen back to the list price when no cost is reported.
    """
    import io
    import json

    import httpx

    from jevtools.jtools import main

    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vck_test")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def zero_cost(request: httpx.Request) -> httpx.Response:
        questions = json.loads(request.content)["questions"]
        return httpx.Response(
            200,
            json={
                "answers": {qid: {"type": "boolean", "probability": 0.9} for qid in questions},
                "usage": {"inputTokens": 1000, "outputTokens": 0},
                "providerMetadata": {"gateway": {"cost": "0", "routing": {"canonicalSlug": "typesafe-ai/jev"}}},
            },
        )

    out = io.StringIO()
    assert main(["doctor"], transport=httpx.MockTransport(zero_cost), out=out, err=io.StringIO()) == 0
    text = out.getvalue()
    assert "$0.0000000" not in text, "a call the gateway did not price was reported as free"
    assert "$0.0000420" in text, f"1,000 tokens at the list price is $0.000042; got: {text[-200:]}"
