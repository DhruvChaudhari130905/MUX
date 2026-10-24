"""Durable, room-scoped storage for the agent's rolling memory."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mux.memory.pins import Pin, merge_pins
from mux.memory.task_log import Log, LogDraft


class RoomMemoryStore:
    """Keep the current log and task logs for the active sitting under ``.mux/memory``."""

    def __init__(self, room_id: str, root: Path | None = None) -> None:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", room_id)
        self.directory = (root or Path.cwd() / ".mux" / "memory") / safe_id
        self.state_path = self.directory / "state.json"
        self.markdown_path = self.directory / "current.md"
        state = self._read_state()
        self.current_log = _log_from_json(state.get("current_log"))
        self.day_log = _log_from_json(state.get("day_log"))
        self.task_logs = [log for item in state.get("task_logs", []) if (log := _log_from_json(item)) is not None]
        self.pending_pins = []
        for item in state.get("pending_pins", []):
            try:
                self.pending_pins.append(_pin_from_json(item))
            except (KeyError, TypeError, ValueError):
                continue

    def queue_pin(self, pin: Pin | None) -> None:
        if pin is not None:
            self.pending_pins = merge_pins(self.pending_pins, [pin])
            self._save()

    def save_task_log(self, log: Log) -> None:
        self.current_log = log
        self.task_logs.append(log)
        logged_ids = {pin.conflict_id for pin in log.pins}
        self.pending_pins = [pin for pin in self.pending_pins if pin.conflict_id not in logged_ids]
        self._save()

    def save_day_log(self, log: Log) -> None:
        self.current_log = log
        self.day_log = log
        self.task_logs.clear()
        self._save()

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        state = {
            "current_log": _log_to_json(self.current_log),
            "day_log": _log_to_json(self.day_log),
            "task_logs": [_log_to_json(log) for log in self.task_logs],
            "pending_pins": [asdict(pin) for pin in self.pending_pins],
        }
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.state_path)
        if self.current_log is not None:
            body = "# Room memory\n\n" + self.current_log.body.rstrip() + "\n"
            temporary_md = self.markdown_path.with_suffix(".md.tmp")
            temporary_md.write_text(body, encoding="utf-8")
            os.replace(temporary_md, self.markdown_path)


def _log_to_json(log: Log | None) -> dict[str, Any] | None:
    if log is None:
        return None
    return {"draft": log.draft.model_dump(), "pins": [asdict(pin) for pin in log.pins]}


def _log_from_json(value: Any) -> Log | None:
    if not isinstance(value, dict) or not isinstance(value.get("draft"), dict):
        return None
    try:
        pins = [_pin_from_json(item) for item in value.get("pins", [])]
        return Log(LogDraft.model_validate(value["draft"]), pins)
    except (TypeError, ValueError):
        return None


def _pin_from_json(value: Any) -> Pin:
    if not isinstance(value, dict):
        raise ValueError("invalid pin")
    kind = value.get("kind")
    if kind not in ("vote", "override"):
        raise ValueError("invalid pin kind")
    return Pin(kind, str(value["conflict_id"]), str(value["text"]))
