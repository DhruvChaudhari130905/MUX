"""Handles one message at a time per room and returns a structured action."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from mux.agents.coordinator.prompts import Message, RoomView, build_messages
from mux.agents.coordinator.schema import AddPlanItem, CoordinatorAction
from mux.agents.llm import LLM, ModelRole, Usage

MAX_ATTEMPTS = 2

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

T = TypeVar("T", bound=BaseModel)


@dataclass
class Structured(Generic[T]):
    value: T | None  # None when the model failed every attempt
    usage: Usage
    attempts: int


@dataclass
class Decision:
    action: CoordinatorAction
    usage: Usage
    attempts: int
    fallback: bool = False  # True when the model failed twice and the safe default was used


class Coordinator:
    def __init__(self, llm: LLM, role: ModelRole = ModelRole.LIGHTNING, reasoning: bool | None = None) -> None:
        self._llm = llm
        self._role = role
        self._reasoning = reasoning

    async def classify(self, room: RoomView, message: Message) -> Decision:
        # API errors are not caught here: the actor decides whether to retry the message later
        result = await ask_json(
            self._llm, self._role, build_messages(room, message), CoordinatorAction,
            check=lambda action: unknown_ids(action, room, message),
            reasoning=self._reasoning, max_tokens=400,
        )
        if result.value is None:
            return Decision(fallback_action(message), result.usage, result.attempts, fallback=True)
        return Decision(result.value, result.usage, result.attempts)


async def ask_json(
        llm: LLM,
        role: ModelRole,
        messages: list[dict[str, Any]],
        schema: type[T],
        *,
        check: Callable[[T], str] | None = None,
        reasoning: bool | None = None,
        max_tokens: int | None = None,
) -> Structured[T]:
    """Ask for JSON matching schema. On a bad answer, show the model its error and retry once."""
    usage = Usage()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = await llm.chat(role, messages, schema=schema, reasoning=reasoning, max_tokens=max_tokens)
        usage = Usage(usage.prompt_tokens + reply.usage.prompt_tokens,
                      usage.completion_tokens + reply.usage.completion_tokens)
        value, problem = parse(reply.text, schema, check)
        if value is not None:
            return Structured(value, usage, attempt)
        messages = [
            *messages,
            {"role": "assistant", "content": reply.text},
            {"role": "user", "content": f"That answer was invalid: {problem}. Answer again with JSON only."},
        ]
    return Structured(None, usage, MAX_ATTEMPTS)


def parse(text: str, schema: type[T], check: Callable[[T], str] | None = None) -> tuple[T | None, str]:
    try:
        value = schema.model_validate_json(clean_json(text))
    except ValidationError as e:
        return None, _short_error(e)
    problem = check(value) if check else ""
    return (None, problem) if problem else (value, "")


def clean_json(text: str) -> str:
    text = _THINK.sub("", text).strip()
    return _FENCE.sub("", text).strip()


def unknown_ids(action: CoordinatorAction, room: RoomView, message: Message) -> str:
    item = action.add_plan_item
    if item and item.after_task_id and item.after_task_id not in {p.id for p in room.plan}:
        return f"after_task_id {item.after_task_id!r} is not in the plan"
    conflict = action.open_conflict
    if conflict:
        known = {m.id for m in room.pending} | {message.id}
        missing = [i for i in conflict.with_message_ids if i not in known]
        if missing:
            return f"with_message_ids {missing} are not pending messages"
    return ""


def fallback_action(message: Message) -> CoordinatorAction:
    # queue is the only label that loses nothing and breaks nothing
    return CoordinatorAction(
        label="queue",
        rationale="The coordinator could not decide, so the request was queued.",
        add_plan_item=AddPlanItem(title=message.text[:80]),
    )


def _short_error(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or 'json'}: {err['msg']}" for err in e.errors()[:3])
