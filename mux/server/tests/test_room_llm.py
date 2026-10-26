"""Which model a room's agents use: the room's own provider, else the server's."""

from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.fernet import Fernet

from mux.agents.llm import LLMReply, ModelError, ModelRole, NoModel, OpenAILLM, Usage
from mux.agents.room_llm import RoomLLM, check_models, redact
from mux.config import settings
from mux.secrets import encrypt_value

MODELS = {"lightning": "fast-1", "super": "smart-1", "ultra": "smart-1"}


class FakeCompletions:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        message = SimpleNamespace(content="OK", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1))


def fake_client(fail: Exception | None = None) -> Any:
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(fail)))


class Recorder:
    """Stands in for OpenAILLM: records what it was built with and answers every call."""
    built: list[tuple[str, str, dict, bool]] = []

    def __init__(self, base_url: str, api_key: str, models: dict, *, thinking: bool = False) -> None:
        Recorder.built.append((base_url, api_key, {r.value: m for r, m in models.items()}, thinking))
        self.api_key = api_key

    async def chat(self, role: ModelRole, messages: list, **kw: Any) -> LLMReply:
        if self.api_key == "sk-bad":
            raise ModelError("401 invalid key sk-bad")
        return LLMReply(text=f"room:{role.value}", model="m", usage=Usage(1, 1))


class Default:
    def __init__(self) -> None:
        self.roles: list[ModelRole] = []

    async def chat(self, role: ModelRole, messages: list, **kw: Any) -> LLMReply:
        self.roles.append(role)
        return LLMReply(text="server", model="m", usage=Usage(1, 1))


@pytest.fixture
def secrets_key(monkeypatch):
    monkeypatch.setattr(settings, "room_secrets_key", Fernet.generate_key().decode())
    Recorder.built = []


def room(settings_: dict | None) -> Any:
    return SimpleNamespace(ai_settings=settings_, room_id="room_x")


def saved(key: str = "sk-room", version: str = "v1", provider: str = "openai") -> dict:
    return {"provider": provider, "base_url": "https://93.184.216.34/v1", "api_key": encrypt_value(key),
            "models": MODELS, "version": version}


async def test_room_settings_win_over_the_server(secrets_key):
    llm = RoomLLM(room(saved()), Default(), make=Recorder)  # type: ignore[arg-type]
    assert llm.available()
    assert (await llm.chat(ModelRole.SUPER, [])).text == "room:super"
    assert Recorder.built == [("https://93.184.216.34/v1", "sk-room", MODELS, False)]


async def test_server_default_and_no_model(secrets_key):
    assert (await RoomLLM(room(None), Default()).chat(ModelRole.SUPER, [])).text == "server"  # type: ignore[arg-type]
    none = RoomLLM(room(None), None)
    assert not none.available()
    with pytest.raises(NoModel):
        await none.chat(ModelRole.SUPER, [])


async def test_server_default_runs_on_ultra_except_lightning(secrets_key, monkeypatch):
    monkeypatch.setattr(settings, "model_ultra", "nvidia/nemotron-ultra")
    default = Default()
    llm = RoomLLM(room(None), default)  # type: ignore[arg-type]
    for role in (ModelRole.LIGHTNING, ModelRole.SUPER, ModelRole.ULTRA):
        await llm.chat(role, [])
    assert default.roles == [ModelRole.LIGHTNING, ModelRole.ULTRA, ModelRole.ULTRA]


async def test_server_default_keeps_roles_without_an_ultra_model(secrets_key, monkeypatch):
    monkeypatch.setattr(settings, "model_ultra", "")
    default = Default()
    await RoomLLM(room(None), default).chat(ModelRole.SUPER, [])  # type: ignore[arg-type]
    assert default.roles == [ModelRole.SUPER]


async def test_room_settings_keep_their_own_roles(secrets_key, monkeypatch):
    monkeypatch.setattr(settings, "model_ultra", "nvidia/nemotron-ultra")
    llm = RoomLLM(room(saved()), Default(), make=Recorder)  # type: ignore[arg-type]
    assert (await llm.chat(ModelRole.LIGHTNING, [])).text == "room:lightning"


async def test_client_is_rebuilt_only_when_settings_change(secrets_key):
    actor = room(saved(version="v1"))
    llm = RoomLLM(actor, None, make=Recorder)
    await llm.chat(ModelRole.SUPER, [])
    await llm.chat(ModelRole.LIGHTNING, [])
    actor.ai_settings = saved(key="sk-new", version="v2")
    await llm.chat(ModelRole.SUPER, [])
    assert [b[1] for b in Recorder.built] == ["sk-room", "sk-new"]


async def test_token_factory_gets_thinking(secrets_key):
    llm = RoomLLM(room(saved(provider="token_factory")), None, make=Recorder)
    await llm.chat(ModelRole.SUPER, [])
    assert Recorder.built[0][3] is True


async def test_errors_hide_the_key(secrets_key):
    llm = RoomLLM(room(saved(key="sk-bad")), Default(), make=Recorder)  # type: ignore[arg-type]
    with pytest.raises(ModelError) as caught:
        await llm.chat(ModelRole.SUPER, [])
    assert "sk-bad" not in str(caught.value) and "[hidden]" in str(caught.value)


async def test_unreadable_key_never_falls_back_to_the_server(secrets_key, monkeypatch):
    actor = room(saved())
    monkeypatch.setattr(settings, "room_secrets_key", Fernet.generate_key().decode())
    llm = RoomLLM(actor, Default(), make=Recorder)  # type: ignore[arg-type]
    with pytest.raises(NoModel, match="can't read"):
        await llm.chat(ModelRole.SUPER, [])


async def test_openai_llm_sends_thinking_only_when_asked():
    plain_client, thinking_client = fake_client(), fake_client()
    await OpenAILLM("https://x/v1", "k", {ModelRole.SUPER: "m"}, client=plain_client).chat(ModelRole.SUPER, [], reasoning=True)
    await OpenAILLM("https://x/v1", "k", {ModelRole.SUPER: "m"}, thinking=True, client=thinking_client).chat(
        ModelRole.SUPER, [], reasoning=True)
    assert "extra_body" not in plain_client.chat.completions.calls[0]
    assert thinking_client.chat.completions.calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


async def test_check_models_tries_each_distinct_model_once():
    client = fake_client()
    llm = OpenAILLM("https://x/v1", "k", {ModelRole(r): m for r, m in MODELS.items()}, client=client)
    assert await check_models(llm, {ModelRole(r): m for r, m in MODELS.items()}, "k") == {}
    assert [c["model"] for c in client.chat.completions.calls] == ["fast-1", "smart-1"]


async def test_check_models_reports_per_role_with_the_key_hidden():
    failing = OpenAILLM("https://x/v1", "sk-1", {ModelRole(r): m for r, m in MODELS.items()},
                        client=fake_client(RuntimeError("401 Incorrect API key: sk-1")))
    errors = await check_models(failing, {ModelRole(r): m for r, m in MODELS.items()}, "sk-1")
    assert set(errors) == {"lightning", "super", "ultra"}
    assert all("sk-1" not in e and "[hidden]" in e for e in errors.values())


def test_redact():
    assert redact("key sk-1 and 'sk-1'", "sk-1") == "key [hidden] and '[hidden]'"
    assert redact("nothing", "") == "nothing"


# --- review fixes -----------------------------------------------------------------

async def test_provider_redirects_are_not_followed():
    import httpx2 as httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "provider.example":
            return httpx.Response(307, headers={"location": "http://169.254.169.254/latest/meta-data"})
        return httpx.Response(400, json={"error": {"message": "INTERNAL-METADATA token=AKIA"}})

    llm = OpenAILLM("https://provider.example/v1", "k", {ModelRole.SUPER: "m"}, transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as caught:
        await llm.chat(ModelRole.SUPER, [])
    assert "INTERNAL-METADATA" not in str(caught.value)


async def test_room_base_url_is_checked_again_before_use(secrets_key, monkeypatch):
    monkeypatch.setattr(settings, "allow_private_urls", False)
    bad = saved() | {"base_url": "https://10.0.0.5/v1"}
    with pytest.raises(NoModel, match="address"):
        await RoomLLM(room(bad), Default(), make=Recorder).chat(ModelRole.SUPER, [])  # type: ignore[arg-type]


async def test_errors_carry_no_unredacted_cause(secrets_key):
    import traceback

    class Leaky:
        def __init__(self, *a: Any, **k: Any) -> None: ...

        async def chat(self, *a: Any, **k: Any) -> LLMReply:
            raise RuntimeError("401 bad key sk-room")

    with pytest.raises(ModelError) as caught:
        await RoomLLM(room(saved()), None, make=Leaky).chat(ModelRole.SUPER, [])
    assert "sk-room" not in "".join(traceback.format_exception(caught.value))


async def test_the_servers_key_is_hidden_too(monkeypatch):
    monkeypatch.setattr(settings, "token_factory_api_key", "tf-server-key")

    class LeakyDefault:
        async def chat(self, *a: Any, **k: Any) -> LLMReply:
            raise RuntimeError("bad key tf-server-key")

    with pytest.raises(ModelError) as caught:
        await RoomLLM(room(None), LeakyDefault()).chat(ModelRole.SUPER, [])  # type: ignore[arg-type]
    assert "tf-server-key" not in str(caught.value)


@pytest.mark.parametrize("code,words,stuck", [
    (402, "out of credit (402)", True),
    (401, "refused the API key (401)", True),
    (404, "wasn't found (404)", True),
    (429, "rate limiting this room (429)", False),
])
async def test_provider_errors_become_plain_words(secrets_key, code, words, stuck):
    import httpx2 as httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json={"detail": "Payment Required: You have exhausted your budget."})

    default = OpenAILLM("https://provider.example/v1", "sk-server", {r: "m" for r in ModelRole},
                        transport=httpx.MockTransport(handler), max_retries=0)
    with pytest.raises(ModelError) as e:
        await RoomLLM(room(None), default).chat(ModelRole.SUPER, [{"role": "user", "content": "hi"}])
    assert words in str(e.value) and e.value.stuck is stuck


async def test_openrouter_daily_free_limit_pauses_until_the_reset(secrets_key):
    import httpx2 as httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"X-RateLimit-Reset": "1792886400000"}, json={"error": {
            "message": "Rate limit exceeded: free-models-per-day", "code": 429,
        }})

    default = OpenAILLM("https://provider.example/v1", "sk-server", {r: "m" for r in ModelRole},
                        transport=httpx.MockTransport(handler), max_retries=0)
    with pytest.raises(ModelError) as e:
        await RoomLLM(room(None), default).chat(ModelRole.SUPER, [{"role": "user", "content": "hi"}])
    assert e.value.stuck
    assert "1,000 free-model requests" in str(e.value)
    assert "2026-10-25 00:00 UTC" in str(e.value)


async def test_other_errors_keep_the_provider_text_and_are_not_stuck(secrets_key):
    class Broken:
        async def chat(self, *a: Any, **kw: Any) -> LLMReply:
            raise RuntimeError("connection reset")

    with pytest.raises(ModelError) as e:
        await RoomLLM(room(None), Broken()).chat(ModelRole.SUPER, [])
    assert str(e.value) == "RuntimeError: connection reset" and not e.value.stuck
