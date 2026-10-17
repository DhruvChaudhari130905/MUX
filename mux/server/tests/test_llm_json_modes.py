"""Models that refuse the json_schema response format (Groq's 400) still get asked for structured JSON."""

from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError
from pydantic import BaseModel

from mux.agents.llm import ModelRole, OpenAILLM


class Answer(BaseModel):
    ok: bool


def _bad_request(message: str) -> BadRequestError:
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    return BadRequestError(message, response=response, body=None)


class FakeCompletions:
    def __init__(self, refuse: set[str]) -> None:
        self.refuse = refuse
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        kind = kwargs.get("response_format", {}).get("type", "prompt")
        if kind in self.refuse:
            raise _bad_request(f"This model does not support response format `{kind}`")
        message = SimpleNamespace(content='{"ok": true}', tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


def _llm(completions: FakeCompletions) -> OpenAILLM:
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OpenAILLM("https://x", "k", {ModelRole.SUPER: "llama"}, client=client)


@pytest.mark.asyncio
async def test_falls_back_to_json_mode_and_remembers_it():
    completions = FakeCompletions(refuse={"json_schema"})
    llm = _llm(completions)
    reply = await llm.chat(ModelRole.SUPER, [{"role": "user", "content": "hi"}], schema=Answer)
    assert reply.text == '{"ok": true}'
    assert [c.get("response_format", {}).get("type") for c in completions.calls] == ["json_schema", "json_object"]
    assert '"ok"' in completions.calls[-1]["messages"][-1]["content"]

    await llm.chat(ModelRole.SUPER, [{"role": "user", "content": "again"}], schema=Answer)
    assert completions.calls[-1]["response_format"] == {"type": "json_object"}
    assert len(completions.calls) == 3


@pytest.mark.asyncio
async def test_falls_back_to_the_prompt_alone():
    completions = FakeCompletions(refuse={"json_schema", "json_object"})
    reply = await _llm(completions).chat(ModelRole.SUPER, [{"role": "user", "content": "hi"}], schema=Answer)
    assert reply.text == '{"ok": true}'
    assert "response_format" not in completions.calls[-1]


@pytest.mark.asyncio
async def test_other_bad_requests_are_not_retried():
    class Broken(FakeCompletions):
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            raise _bad_request("context length exceeded")

    completions = Broken(refuse=set())
    with pytest.raises(BadRequestError):
        await _llm(completions).chat(ModelRole.SUPER, [{"role": "user", "content": "hi"}], schema=Answer)
    assert len(completions.calls) == 1
