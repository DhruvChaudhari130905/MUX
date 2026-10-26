"""OpenAI-compatible model client (Token Factory by default). Model selection, reasoning on/off, usage tracking."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol

import httpx2
from openai import APIStatusError, AsyncOpenAI, BadRequestError
from pydantic import BaseModel

from mux.config import settings

class ModelRole(str, Enum):
    LIGHTNING = "lightning"
    SUPER = "super"
    ULTRA = "ultra"

@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self):
        return self.prompt_tokens + self.completion_tokens

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None #none when the model sent invalid JSON
    raw_arguments : str

@dataclass
class LLMReply:
    text: str
    model: str
    usage: Usage
    tool_calls : list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None

DeltaCallback = Callable[[str], Awaitable[None]]

class LLM(Protocol):
    async def chat(
            self,
            role: ModelRole,
            messages: list[dict[str, Any]],
            *,
            tools: list[dict[str, Any]] | None = None,
            schema: type[BaseModel] | None = None,
            reasoning: bool | None = None,
            max_tokens: int | None = None,
            on_delta : DeltaCallback | None = None,
    ) -> LLMReply: ...

class ModelError(Exception):
    """A model call failed (bad key, unknown model, rate limit, provider down). The text is safe to show.

    `stuck` is set when retrying can't help until someone acts (no credit, key refused, model missing).
    """

    def __init__(self, message: str = "", *, stuck: bool = False) -> None:
        super().__init__(message)
        self.stuck = stuck


# What the provider's HTTP status means for the room, and whether the agents should stop until someone acts
_STATUS_TEXT: dict[int, tuple[str, bool]] = {
    401: ("the AI provider refused the API key (401). Check the key, or pick another model in AI model", True),
    402: ("the AI account is out of credit (402). Add funds to it, or pick another provider in AI model", True),
    403: ("the AI provider refused access (403). Check the key and the account, or pick another model in AI model", True),
    404: ("the AI model wasn't found (404). Pick a model this key can use in AI model", True),
    429: ("the AI provider is rate limiting this room (429). It will try again with the next message", False),
}


def describe_api_error(e: Exception) -> tuple[str, bool] | None:
    """Plain words for a provider error the room can act on, and whether it is stuck; None for anything else."""
    if isinstance(e, APIStatusError):
        if e.status_code == 429 and "free-models-per-day" in str(e):
            reset = _rate_limit_reset(e)
            when = f" It resets at {reset}." if reset else ""
            return ("the OpenRouter free-model daily limit is exhausted (429)."
                    f" Add $10 in OpenRouter credits to unlock 1,000 free-model requests per day.{when}", True)
        return _STATUS_TEXT.get(e.status_code)
    return None


def _rate_limit_reset(e: APIStatusError) -> str | None:
    """Format OpenRouter's epoch-millisecond reset header, if the provider sent one."""
    try:
        raw = e.response.headers.get("X-RateLimit-Reset")
        if raw is None:
            return None
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        return None


class NoModel(ModelError):
    """The room has no model to use."""


class OpenAILLM:
    """Any OpenAI-compatible chat completions API. `thinking` sends Nemotron's switch (Token Factory only)."""

    def __init__(self, base_url: str, api_key: str, models: dict[ModelRole, str], *, thinking: bool = False,
                 client: Any = None, max_retries: int = 3, timeout: float = 120.0,
                 transport: httpx2.AsyncBaseTransport | None = None) -> None:
        # Redirects aren't followed: a room's provider could otherwise send requests (and show their answers)
        # to addresses the URL check refused, like the cloud metadata service
        http = httpx2.AsyncClient(follow_redirects=False, timeout=timeout, transport=transport)
        self._client = client or AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=max_retries,
                                             timeout=timeout, http_client=http)
        self._models = models
        self._thinking = thinking
        # The structured-output mode each model accepts, learned from its 400s (see _JSON_MODES)
        self._json_modes: dict[str, str] = {}

    async def chat(self, role, messages, *, tools=None, schema=None,
                   reasoning=None, max_tokens=None, on_delta=None) -> LLMReply:
        model = self._models[role]
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if reasoning is not None and self._thinking:
            #(spike) confirm the switch name Token Factory accepts for Nemotron
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": reasoning}}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if schema is None:
            return await self._send(kwargs, model, on_delta)
        while True:
            mode = self._json_modes.get(model, _JSON_MODES[0])
            try:
                return await self._send(_with_schema(kwargs, schema, mode), model, on_delta)
            except BadRequestError as e:
                # Many providers (Groq, Ollama, ...) support only some response formats: step down and remember
                if mode == _JSON_MODES[-1] or not _format_refused(e):
                    raise
                self._json_modes[model] = _JSON_MODES[_JSON_MODES.index(mode) + 1]

    async def _send(self, kwargs, model, on_delta) -> LLMReply:
        if on_delta is None:
            resp = await self._client.chat.completions.create(**kwargs)
            choice = resp.choices[0]
            calls = [_tool_call(tc.id, tc.function.name, tc.function.arguments)
                     for tc in choice.message.tool_calls or []]
            content = choice.message.content or ""
            if not calls:
                calls, content = text_tool_calls(content)
            return LLMReply(
                text = content,
                model = model,
                usage = _usage(resp.usage),
                tool_calls = calls,
                finish_reason = choice.finish_reason,
            )
        return await self._stream(kwargs, model, on_delta)

    async def list_models(self) -> list[str]:
        """The model ids the key can use (GET /models), sorted."""
        return sorted({m.id async for m in self._client.models.list() if m.id})

    async def _stream(self, kwargs, model, on_delta) -> LLMReply:
        stream = await self._client.chat.completions.create(
            **kwargs, stream= True, stream_options={"include_usage":True}
        )
        text : list[str] = []
        slots: dict[int, dict[str, str]] = {}
        usage, finish = Usage(), None
        async for chunk in stream:
            if chunk.usage:
                usage = _usage(chunk.usage)
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                text.append(delta.content)
                await on_delta(delta.content)
            #tool call arrive in pieces, keyed by index: id and name first then argyment fragments
            for tc in delta.tool_calls or []:
                slot = slots.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments
            if choice.finish_reason:
                finish = choice.finish_reason
        calls = [_tool_call(s["id"], s["name"], s["args"]) for _, s in sorted(slots.items())]
        content = "".join(text)
        if not calls:
            calls, content = text_tool_calls(content)
        return LLMReply(text=content, model=model, usage=usage, tool_calls=calls, finish_reason=finish)


# Tried in order: a JSON schema response format, plain JSON mode, then only the schema written into the prompt
_JSON_MODES = ["json_schema", "json_object", "prompt"]


def _format_refused(e: BadRequestError) -> bool:
    """Whether a 400 is about the response format (Groq: param 'response_format', "does not support response format")."""
    text = str(e).lower()
    return e.param == "response_format" or "response_format" in text or "response format" in text


def _with_schema(kwargs: dict[str, Any], schema: type[BaseModel], mode: str) -> dict[str, Any]:
    """kwargs asking for JSON matching schema: by response_format where the model allows it, else in the prompt."""
    json_schema = schema.model_json_schema()
    if mode == "json_schema":
        return {**kwargs, "response_format": {
            "type": "json_schema", "json_schema": {"name": schema.__name__, "schema": json_schema, "strict": False}}}
    # JSON mode needs "JSON" in the messages (Groq, OpenAI), and the schema is all the model has to go on
    note = {"role": "system", "content": "Answer with a single JSON object, and nothing else, matching this "
                                         f"JSON schema:\n{json.dumps(json_schema)}"}
    out = {**kwargs, "messages": [*kwargs["messages"], note]}
    if mode == "json_object":
        out["response_format"] = {"type": "json_object"}
    return out


def TokenFactoryLLM() -> OpenAILLM:
    """The server's own model client, from the TOKEN_FACTORY_* and MODEL_* settings."""
    return OpenAILLM(
        settings.token_factory_base_url, settings.token_factory_api_key,
        {ModelRole.LIGHTNING: settings.model_lightning, ModelRole.SUPER: settings.model_super,
         ModelRole.ULTRA: settings.model_ultra},
        thinking=True,
    )


def _usage(u) -> Usage:
    return Usage(u.prompt_tokens, u.completion_tokens) if u else Usage()

_TEXT_CALL_TAG = re.compile(r"<tool_call>\s*", re.IGNORECASE)
_TEXT_CALL_NAME = re.compile(r"(?:functions\.)?([A-Za-z_]\w*)\s*\(", re.IGNORECASE)
_TEXT_CALL_END = re.compile(r"\s*\)?\s*(?:</tool_call>)?")


def text_tool_calls(text: str) -> tuple[list[ToolCall], str]:
    """Tool calls a model wrote into its message instead of the tools API, and the text without them.

    Nemotron sometimes answers with `<tool_call> FUNCTIONS.write_file({...})` or
    `<tool_call>{"name": ..., "arguments": {...}}</tool_call>` as plain text, which wasted the turn
    (room_db8a580bd5b9). A call whose JSON is cut off (the reply hit max_tokens) is left as text.
    """
    if "<tool_call>" not in text.lower():
        return [], text
    decoder = json.JSONDecoder()
    calls: list[ToolCall] = []
    kept: list[str] = []
    pos = 0
    for tag in _TEXT_CALL_TAG.finditer(text):
        if tag.start() < pos:
            continue
        start = tag.end()
        name, args = "", None
        named = _TEXT_CALL_NAME.match(text, start)
        try:
            if named:
                name = named.group(1)
                args, end = decoder.raw_decode(text, named.end())
            else:
                obj, end = decoder.raw_decode(text, start)
                if isinstance(obj, dict):
                    name = str(obj.get("name") or "")
                    args = obj.get("arguments", obj.get("parameters", {}))
                    if isinstance(args, str):
                        args = json.loads(args)
        except ValueError:
            continue
        if not name or not isinstance(args, dict):
            continue
        tail = _TEXT_CALL_END.match(text, end)
        kept.append(text[pos:tag.start()])
        pos = tail.end() if tail else end
        calls.append(ToolCall(id=f"text_call_{len(calls)}", name=name, arguments=args, raw_arguments=json.dumps(args)))
    kept.append(text[pos:])
    return calls, "".join(kept).strip() if calls else text


def _tool_call(id:str, name: str, raw: str | None) -> ToolCall:
    raw = raw or ""
    try:
        args = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        args = None #the coder loop answers with "invalid argyments" and the model retries
    if not isinstance(args, dict):
        args = None  # valid JSON but not an object (a list, a string) is no use as arguments either
    return ToolCall(id=id, name=name, arguments=args, raw_arguments=raw)
