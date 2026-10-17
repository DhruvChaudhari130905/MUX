"""The room's agents: the coordinator decides WHAT, the coder decides HOW (architecture.md §3, §6, §7).

One `RoomRuntime` per running room. It watches the actor's events in order and never changes state
directly: everything goes through the actor's methods, like a person's command, so every effect is an
event the room sees live.

- Room created: the plan starts empty; it fills from what the team asks for in the feed.
- Agent message posted: the coordinator labels it (message.labeled) and the runtime applies the label:
  merge/interrupt go to the coder's inbox, queue adds a plan item, conflict opens a vote with Tavily
  research, chat posts a coordinator.reply.
- Plan approved (or new todo work): the coder works through todo tasks. Each turn hands over merged
  messages and interrupts; each finished task gets a checkpoint.
- A task that stops (turn limit, loop, API error) is parked behind a "Retry or skip?" question, so the
  coder never spins on it.

Conflict and question timers live in memory: after a restart, open cards wait for the owner to act.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional, cast
from uuid import uuid4

from mux.agents.coder.context import CoderContext, build_context
from mux.agents.coder.loop import CoderLoop, CoderTask, ToolExecutor, TurnBoundary
from mux.agents.coder.prompts import REVIEW_SYSTEM_PROMPT, UNDERSTAND_SYSTEM_PROMPT, coder_system_prompt
from mux.agents.coder.tools import CoderToolExecutor
from mux.agents.coder.tools.codecheck import project_problems
from mux.agents.coder.tools.files import ActorFileTools
from mux.agents.coder.tools.review import REVIEW_MAX_FILES, REVIEW_MAX_TURNS, review_scope
from mux.agents.coordinator.agent import Coordinator
from mux.agents.coordinator.conflicts import Vote, research_conflict, tally, vote_weight
from mux.agents.coordinator.kickoff import ask_kickoff_questions
from mux.agents.coordinator.planner import create_plan, next_task_id
from mux.agents.coordinator.prompts import Message, PlanItem, RoomView
from mux.agents.coordinator.schema import CoordinatorAction, Domain, DomainRole, OpenConflict
from mux.agents.llm import LLM, ModelError, Usage
from mux.events.models import (
    BaseEvent,
    CommandAnswerQuestionEvent,
    CommandVoteEvent,
    ConflictResolvedEvent,
    EventType,
    QuestionAnsweredEvent,
    UserMessageSentEvent,
)
from mux.events.wire import ephemeral, user_view
from mux.integrations.tavily import WebSearch
from mux.mcp.catalog import enabled_servers
from mux.mcp.toolset import ALLOW, DENY, McpToolset
import mux.skills.library as skills_library
from mux.agents.coder.tools.skills import skills_prompt
from mux.rooms.actor import RoomActor
from mux.sandbox.runner import Runner

logger = logging.getLogger(__name__)

COORDINATOR = "coordinator"
CODER = "coder"
RECENT_MESSAGES = 10
TEAM_NOTES = 5
RETRY, SKIP = "Retry", "Skip"
FIX_TITLE = "Fix errors in the code"
MAX_FIX_ATTEMPTS = 2  # fix tasks in a row that leave errors behind before the room is asked to step in
MAX_REPORTED_ERRORS = 10

# Sends an unstored envelope (agent.text.delta) to the room's sockets
Publish = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class _Conflict:
    options: list[str]
    domain: Domain
    summary: str
    task_id: Optional[str]
    votes: list[Vote] = field(default_factory=list)
    timer: Optional[asyncio.Task] = None


@dataclass
class _Question:
    task_id: Optional[str]
    options: list[str]
    default: str
    parked: bool  # True: the runtime asked "Retry or skip?" about a stopped task
    timer: Optional[asyncio.Task] = None
    waiter: Optional[asyncio.Future[str]] = None  # set by ask_and_wait: the answer goes here instead of the plan


class RoomRuntime:
    def __init__(
        self,
        actor: RoomActor,
        llm: LLM,
        *,
        search: Optional[WebSearch] = None,
        runner: Optional[Runner] = None,
        conventions: str = "",
        publish: Optional[Publish] = None,
        vote_timeout: float = 60.0,
        question_timeout: float = 300.0,
        max_turns: int = 40,
        model_error_interval: float = 60.0,
    ) -> None:
        self.actor = actor
        self.llm = llm
        self.search = search
        self.runner = runner  # the sandbox for run_build / run_tests; None: the coder isn't offered them
        self.conventions = conventions
        self.publish = publish
        self.vote_timeout = vote_timeout
        self.question_timeout = question_timeout
        self.max_turns = max_turns
        self.model_error_interval = model_error_interval
        self._last_model_error = float("-inf")
        # Set when the model failed in a way retrying can't fix (no credit, key refused): the coder starts no
        # tasks until the AI settings change, someone runs the plan, or the model answers again
        self._stuck: Optional[str] = None
        self._kickoff_task: Optional[asyncio.Task] = None
        self._current_kind: Optional[str] = None  # kind of the running task ("review", "understand" or None)
        self.understand_timeout = 600.0  # the kickoff stops waiting for the project read after this long
        self._task_waiters: dict[str, asyncio.Future[tuple[str, str]]] = {}  # plan item id -> (status, summary)
        self.coordinator = Coordinator(llm)

        self._events: asyncio.Queue[BaseEvent] = asyncio.Queue()
        self._recent: deque[Message] = deque(maxlen=RECENT_MESSAGES)
        self._notes: deque[Message] = deque(maxlen=TEAM_NOTES)
        self._conflicts: dict[str, _Conflict] = {}
        self._questions: dict[str, _Question] = {}
        self._wake = asyncio.Event()
        self._current_task: Optional[str] = None
        # Everyone whose requests shaped the running task: the coder's edits must suit all of their folders
        # (mux/rooms/file_access.py). Empty for work the room agreed on (the owner's plan, kickoff).
        self._task_requesters: set[str] = set()
        self._fix_task: Optional[str] = None  # the open "Fix errors" task, so errors get one at a time
        self._fix_attempts = 0  # fix tasks since the code last checked clean
        self._tasks: list[asyncio.Task] = []
        self._timers: set[asyncio.Task] = set()

    # ---- lifecycle ----

    async def start(self) -> None:
        # A task left "doing" by a crash or restart is picked up again
        for item in await self.actor.get_plan():
            if item.get("status") == "doing":
                await self.actor.update_plan_item(item["id"], {"status": "todo"}, CODER)
        self._tasks = [asyncio.create_task(self._consume()), asyncio.create_task(self._coder())]
        self._wake.set()

    async def stop(self) -> None:
        kickoff = [self._kickoff_task] if self._kickoff_task is not None else []
        for task in [*self._tasks, *self._timers, *kickoff]:
            task.cancel()
        await asyncio.gather(*self._tasks, *self._timers, *kickoff, return_exceptions=True)
        self._tasks.clear()
        self._timers.clear()

    def observe(self, event: BaseEvent) -> None:
        """Called for every event the actor emits, in order."""
        self._events.put_nowait(event)

    async def idle(self) -> None:
        """Wait until every observed event is handled (tests)."""
        await self._events.join()

    def _model_available(self) -> bool:
        """False when the room has no model (no room key, no server key): agents stay off, quietly."""
        available = getattr(self.llm, "available", None)
        return bool(available()) if callable(available) else True

    def model_available(self) -> bool:
        return self._model_available()

    @property
    def kickoff_running(self) -> bool:
        return self._kickoff_task is not None and not self._kickoff_task.done()

    async def _kickoff(self) -> None:
        """"Plan it with me": understand the project, ask the team, draft a plan (spec 2026-10-10)."""
        try:
            meta = self.actor.manifest.get_room_metadata()
            description = (meta.get("description") or meta.get("name") or "").strip()
            summary = description
            if await self.actor.list_files():
                await self._step("Reading your project…")
                status, text = await self._understand()
                if status == "done" and text:
                    summary = text
                else:
                    await self._step("Couldn't read the project; planning from the description")
            questions, usage = await ask_kickoff_questions(self.llm, description, summary)
            await self._spend(usage, COORDINATOR)
            answers: list[tuple[str, str]] = []
            if questions is not None:
                total = len(questions.questions)
                for n, q in enumerate(questions.questions, 1):
                    await self._step(f"Question {n} of {total}")
                    answers.append((q.question, await self.ask_and_wait(q.question, q.options, q.default, None)))
            context = f"Project summary:\n{summary or '(none)'}"
            if answers:
                context += "\n\nThe team's answers:\n" + "\n".join(f"- {q} {a}" for q, a in answers)
            result = await create_plan(self.llm, description or "(no description)", context=context)
            await self._spend(result.usage, COORDINATOR)
            for item in result.items:
                new: dict[str, Any] = {"id": next_task_id(await self._plan_items()), "title": item.title, "status": "draft"}
                if getattr(item, "notes", None):
                    new["notes"] = item.notes
                if getattr(item, "owner_role", None):
                    new["owner_role"] = item.owner_role
                await self.actor.add_plan_item(new, COORDINATOR)
            await self._step("Plan drafted from your answers — approve it to start")
        except asyncio.CancelledError:
            raise
        except ModelError as e:
            await self._model_error(e)
            await self._step(f"Kickoff stopped: {e}")
        except Exception:
            logger.exception(f"Room {self.actor.room_id}: kickoff failed")
            await self._step("Kickoff stopped: something went wrong")

    async def _understand(self) -> tuple[str, str]:
        """Run a read-only "understand" task and wait for it: (status, summary)."""
        item_id = next_task_id(await self._plan_items())
        waiter: asyncio.Future[tuple[str, str]] = asyncio.get_running_loop().create_future()
        self._task_waiters[item_id] = waiter
        await self.actor.add_plan_item(
            {"id": item_id, "title": "Understand the project", "status": "todo", "kind": "understand"}, COORDINATOR,
        )
        self._wake.set()
        try:
            # Bounded: the item may never run (deleted from the plan, budget paused, model removed)
            return await asyncio.wait_for(waiter, self.understand_timeout)
        except asyncio.TimeoutError:
            return ("stopped", "timed out")
        finally:
            self._task_waiters.pop(item_id, None)

    async def _release_removed_waiters(self) -> None:
        """A waited-on task that left the plan (deleted, rewound away) will never run: stop waiting for it."""
        if not self._task_waiters:
            return
        waiting = {i["id"] for i in await self.actor.get_plan() if i.get("status") in ("todo", "doing")}
        for item_id, waiter in list(self._task_waiters.items()):
            if item_id not in waiting and not waiter.done():
                waiter.set_result(("stopped", "removed from the plan"))

    async def _step(self, text: str) -> None:
        await self.actor.post_notice("kickoff.step", {"text": text})

    async def _model_error(self, error: ModelError) -> None:
        """Tell the room its model failed, at most once per model_error_interval (always when the agents pause)."""
        pausing = error.stuck and self._stuck is None
        if error.stuck:
            self._stuck = str(error)
        now = time.monotonic()
        if now - self._last_model_error < self.model_error_interval and not pausing:
            return
        self._last_model_error = now
        payload: dict[str, Any] = {"error": str(error)[:300]}
        if error.stuck:
            payload["paused"] = True
        await self.actor.post_notice("ai.error", payload)

    def _resume(self) -> None:
        """Something changed (new AI settings, Run plan, a model reply): the coder may start tasks again."""
        if self._stuck is not None:
            self._stuck = None
            self._last_model_error = float("-inf")
        self._wake.set()

    # ---- events ----

    async def _consume(self) -> None:
        while True:
            event = await self._events.get()
            try:
                await self._handle(event)
            except asyncio.CancelledError:
                raise
            except ModelError as e:
                await self._model_error(e)
            except Exception:
                logger.exception(f"Room {self.actor.room_id}: runtime failed on {event.type}")
            finally:
                self._events.task_done()

    async def _handle(self, event: BaseEvent) -> None:
        t = event.type
        if t == EventType.ROOM_AI_SETTINGS_SAVED:
            self._resume()  # approved tasks waiting for a model can start
            return
        if not self._model_available():
            return
        if t == EventType.KICKOFF_REQUESTED:
            self._stuck = None  # the owner asked again: try the model (the coder waits until it answers)
            if not self.kickoff_running:
                self._kickoff_task = asyncio.create_task(self._kickoff())
            return
        if t == EventType.USER_MESSAGE_SENT:
            e = cast(UserMessageSentEvent, event)
            message = Message(e.message_id, e.user_id, self._domain_role(e.user_id), e.content)
            if e.to == "team":
                self._notes.append(message)
            elif e.user_id not in (COORDINATOR, CODER):
                await self._classify(message)
        elif t == EventType.COMMAND_VOTE:
            e = cast(CommandVoteEvent, event)
            await self._vote(e.plan_item_id or "", e.issued_by, e.option_id)
        elif t == EventType.CONFLICT_RESOLVED:
            await self._conflict_closed(cast(ConflictResolvedEvent, event))
        elif t in (EventType.COMMAND_ANSWER_QUESTION, EventType.QUESTION_ANSWERED):
            e = cast(CommandAnswerQuestionEvent | QuestionAnsweredEvent, event)
            await self._answered(e.question_id, e.answer)
        elif t in (EventType.COMMAND_APPROVE_PLAN, EventType.PLAN_ITEM_ADDED, EventType.PLAN_UPDATED,
                   EventType.BUDGET_RESUMED, EventType.COMMAND_REWIND):
            await self._release_removed_waiters()
            self._wake.set()

    # ---- coordinator ----

    async def _classify(self, message: Message) -> None:
        plan = await self._plan_items()
        view = RoomView(
            plan=plan,
            current_task_id=self._current_task,
            pending=list(self._recent),
            open_cards=[f"conflict: {c.summary} ({' / '.join(c.options)})" for c in self._conflicts.values()],
            team_notes=list(self._notes),
        )
        decision = await self.coordinator.classify(view, message)
        if self._stuck is not None:
            self._resume()  # the model answers again (funds added, limit lifted): the coder carries on
        await self._spend(decision.usage, COORDINATOR)
        action = decision.action
        await self.actor.post_notice("message.labeled", {
            "message_id": message.id, "label": action.label, "rationale": action.rationale, "domain": action.domain,
        })
        self._recent.append(message)
        await self._apply(action, message, plan)

    async def _apply(self, action: CoordinatorAction, message: Message, plan: list[PlanItem]) -> None:
        if action.label in ("merge", "interrupt") and (self._current_task is None
                                                       or self._current_kind in ("review", "understand")):
            # Nothing is running to merge it into (e.g. every task is done), or only a read-only task that can't
            # make changes: it becomes a task of its own, so a change asked for in the feed still gets built
            await self._add_task(message.text, plan, requested_by=[message.author])
        elif action.label in ("merge", "interrupt"):
            self._task_requesters.add(message.author)
            await self.actor.enqueue_message(
                action.label, message.text, message_id=message.id, user_id=message.author,
                rationale=action.rationale, domain=action.domain,
            )
        elif action.label == "queue" and action.add_plan_item:
            # The actor appends; `after_task_id` ordering is a later refinement.
            await self._add_task(action.add_plan_item.title, plan, requested_by=[message.author])
        elif action.label == "conflict" and action.open_conflict and action.domain:
            await self._open_conflict(action.open_conflict, action.domain)
        elif action.label == "review" and action.review:
            await self._add_review(action.review.focus, plan)
        elif action.label == "chat" and action.reply:
            now = datetime.now(timezone.utc).isoformat()
            await self.actor.post_notice("coordinator.reply", {
                "id": str(uuid4()), "room_id": self.actor.room_id, "user_id": COORDINATOR, "text": action.reply,
                "created_at": now, "user": user_view(COORDINATOR, "Coordinator"), "reply_to": message.id,
            })

    async def _add_task(self, title: str, plan: list[PlanItem], requested_by: Iterable[str] = ()) -> None:
        # Before the owner approves the plan, new work joins the draft instead of starting the coder
        status = "draft" if any(p.status == "draft" for p in plan) else "todo"
        title = " ".join(title.split())
        item: dict[str, Any] = {"id": next_task_id(plan), "title": title if len(title) <= 120 else title[:119] + "…", "status": status}
        if people := sorted(set(requested_by)):
            item["requested_by"] = people
        await self.actor.add_plan_item(item, COORDINATOR)

    async def _add_review(self, focus: str, plan: list[PlanItem]) -> None:
        # Read-only, so it doesn't wait for the owner to approve the plan
        focus = " ".join(focus.split())[:110]
        await self.actor.add_plan_item(
            {"id": next_task_id(plan), "title": f"Review: {focus}", "status": "todo", "kind": "review"}, COORDINATOR,
        )
        self._wake.set()

    # ---- conflicts and votes ----

    async def _open_conflict(self, conflict: OpenConflict, domain: Domain) -> None:
        deadline = datetime.now(timezone.utc) + timedelta(seconds=self.vote_timeout)
        cid = await self.actor.detect_conflict(
            conflict.with_message_ids, domain, conflict.summary, COORDINATOR, deadline,
            options=conflict.options, task_id=self._current_task,
        )
        state = _Conflict(conflict.options, domain, conflict.summary, self._current_task)
        self._conflicts[cid] = state
        state.timer = self._later(self.vote_timeout, lambda: self._close_vote(cid, timed_out=True))
        if self.search is not None:
            self._later(0, lambda: self._research(cid, conflict))

    async def _research(self, cid: str, conflict: OpenConflict) -> None:
        assert self.search is not None
        research = await research_conflict(self.llm, self.search, conflict)
        await self._spend(research.usage, COORDINATOR)
        if research.evidence is not None:
            await self.actor.post_notice("conflict.evidence", {"conflict_id": cid, "evidence": [{
                "query": "; ".join(research.queries),
                "summary": research.evidence.summary,
                "citations": [c.url for c in research.evidence.citations],
            }]})

    async def _vote(self, cid: str, user_id: str, option: str) -> None:
        state = self._conflicts.get(cid)
        if state is None or option not in state.options:
            return
        state.votes.append(Vote(user_id, option, self._domain_role(user_id)))
        voters = {self.actor.owner_id, *(u for u, r in self.actor.members.items() if r == "editor")}
        if voters <= {v.user_id for v in state.votes}:
            await self._close_vote(cid, timed_out=False)

    async def _close_vote(self, cid: str, *, timed_out: bool) -> None:
        state = self._conflicts.get(cid)
        if state is None:
            return
        result = tally(state.options, state.votes, state.domain, self.actor.owner_id)
        if result.winner is None:
            if timed_out:  # Q48: a tie nobody can break waits for the owner's override
                await self._reply(f"The vote on \"{state.summary}\" is tied. The owner can override to settle it.")
            return
        await self.actor.resolve_conflict(cid, result.winner, "vote", {
            "totals": result.totals, "decided_by": result.decided_by,
            "weights": {v.user_id: vote_weight(v.domain_role, state.domain) for v in state.votes},
        })

    async def _conflict_closed(self, event: ConflictResolvedEvent) -> None:
        state = self._conflicts.pop(event.conflict_id, None)
        if state is None:
            return
        if state.timer is not None:
            state.timer.cancel()
        # The coder picks the decision up at its next turn boundary
        await self.actor.enqueue_message(
            "merge", f"The room decided \"{event.resolution}\" on: {state.summary}", user_id=COORDINATOR,
        )
        self._wake.set()

    # ---- questions ----

    async def _ask(self, question: str, options: list[str], default: str, task_id: Optional[str], *, parked: bool,
                   waiter: Optional[asyncio.Future[str]] = None) -> str:
        expires = datetime.now(timezone.utc) + timedelta(seconds=self.question_timeout)
        qid = await self.actor.ask_question(
            question, CODER, options=options, default_option=default, task_id=task_id, expires_at=expires,
        )
        state = _Question(task_id, options, default, parked, waiter=waiter)
        self._questions[qid] = state
        state.timer = self._later(self.question_timeout, lambda: self._default_answer(qid))
        return qid

    async def ask_and_wait(self, question: str, options: list[str], default: str, task_id: Optional[str]) -> str:
        """Ask the room and wait for the answer (the default when the card expires). Leaves the plan alone."""
        waiter: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        await self._ask(question, options, default, task_id, parked=False, waiter=waiter)
        return await waiter

    async def _default_answer(self, qid: str) -> None:
        state = self._questions.get(qid)
        if state is None:
            return
        await self.actor.post_notice("question.defaulted", {"question_id": qid})
        await self._answered(qid, state.default)

    async def _answered(self, qid: str, answer: str) -> None:
        state = self._questions.pop(qid, None)
        if state is None:
            return
        if state.timer is not None and state.timer is not asyncio.current_task():
            state.timer.cancel()
        if state.waiter is not None:
            if not state.waiter.done():
                state.waiter.set_result(answer)
            return
        if state.task_id is None:
            return
        if state.parked and answer == SKIP:
            return  # the task stays skipped
        notes = "retrying" if state.parked else f"answer: {answer}"
        await self.actor.update_plan_item(state.task_id, {"status": "todo", "notes": notes}, CODER)
        if not state.parked:
            await self.actor.enqueue_message("merge", f"The room answered \"{answer}\"", user_id=COORDINATOR)
        self._wake.set()

    # ---- coder ----

    async def _coder(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                task = await self._next_task()
                if (task is None or self._stuck is not None or not self._model_available()
                        or not await self.actor.check_budget_allowance()):
                    break
                await self._run(task)

    async def _next_task(self) -> Optional[dict[str, Any]]:
        return next((i for i in await self.actor.get_plan() if i.get("status") == "todo"), None)

    async def _run(self, item: dict[str, Any]) -> None:
        task_id = item["id"]
        self._current_task = task_id
        self._current_kind = item.get("kind")
        self._task_requesters = set(item.get("requested_by") or [])
        await self.actor.update_plan_item(task_id, {"status": "doing"}, CODER)
        asked: list[str] = []

        async def on_question(card: dict[str, Any]) -> None:
            asked.append(await self._ask(card["question"], card["options"], card["default"], task_id, parked=False))

        kind = item.get("kind")
        review = kind in ("review", "understand")  # read-only tasks: no write tools, no MCP, no checkpoint
        outcome: tuple[str, str] = ("stopped", "")
        scope = review_scope(await self.actor.list_files()) if kind == "review" else None
        skills = self._skills()
        files = ActorFileTools(self.actor, on_behalf_of=lambda: sorted(self._task_requesters), task_id=task_id)
        executor = CoderToolExecutor(files, runner=None if review else self.runner,
                                     search=self.search, on_question=on_question,
                                     read_only=review, review_scope=scope, skills=skills)

        async def ask_first(question: str) -> str:
            return await self.ask_and_wait(question, [ALLOW, DENY], DENY, task_id)

        async def unavailable(server: str, reason: str) -> None:
            await self.actor.post_notice("mcp.unavailable", {"server": server, "error": reason, "task_id": task_id}, CODER)

        try:
            # MCP servers connect for this task only and disconnect when it ends (mux/mcp/toolset.py)
            # A review is read-only, and MCP tools can change things elsewhere, so it gets none
            servers = [] if review else enabled_servers(self.actor)
            async with McpToolset(executor, servers, ask=ask_first, on_unavailable=unavailable) as toolset:
                loop = CoderLoop(self.llm, _Narrated(self.actor, toolset), _Boundary(self.actor), toolset.schemas(),
                                 max_turns=max(self.max_turns, REVIEW_MAX_TURNS) if kind == "review" else self.max_turns,
                                 on_text_delta=self._delta(task_id), on_usage=lambda usage: self._spend(usage, CODER))
                result = await loop.run_task(CoderTask(task_id, item["title"]), await self._context(item, executor.can_build, kind=kind, skills=skills))
            outcome = (result.status, result.summary)
            await self.actor.post_notice("agent.text", {"task_id": task_id, "text": result.summary}, CODER)
            if asked:
                await self.actor.update_plan_item(task_id, {"status": "skipped_question"}, CODER)
            elif result.status == "done":
                await self.actor.update_plan_item(task_id, {"status": "done"}, CODER)
                if not review:  # a review changes nothing, so there's nothing to checkpoint
                    await self.actor.create_checkpoint(CODER, f"After: {item['title']}")
                    await self._check_code()
            elif "interrupted" in result.summary:
                await self.actor.update_plan_item(task_id, {"status": "todo", "notes": "re-planning after an interrupt"}, CODER)
            else:
                await self._park(item, result.summary)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            outcome = ("stopped", str(e))
            if isinstance(e, ModelError):
                # No traceback: the text is already safe to show, the original error may not be
                logger.warning(f"Room {self.actor.room_id}: coder's model failed on {task_id}: {e}")
                await self._model_error(e)
            else:
                logger.exception(f"Room {self.actor.room_id}: coder failed on {task_id}")
            if isinstance(e, ModelError) and e.stuck:
                # Every task would fail the same way: this one waits in the queue instead of asking to retry
                await self.actor.update_plan_item(task_id, {"status": "todo", "notes": "waiting for the AI model"}, CODER)
            else:
                await self._park(item, f"error: {e}")
        finally:
            self._current_task = None
            self._current_kind = None
            waiter = self._task_waiters.pop(task_id, None)
            if waiter is not None and not waiter.done():
                waiter.set_result(outcome)

    async def _park(self, item: dict[str, Any], why: str) -> None:
        await self.actor.update_plan_item(item["id"], {"status": "skipped_question", "notes": why[:200]}, CODER)
        await self._ask(f"The coder stopped on \"{item['title']}\" ({why}). Retry it?", [RETRY, SKIP], SKIP,
                        item["id"], parked=True)

    # ---- fixing errors ----

    async def _check_code(self) -> None:
        """After a task: errors anywhere in the project's code become a fix task for the coder."""
        problems = await project_problems(ActorFileTools(self.actor))
        if problems:
            # The fix is bound by the same folders as the task that left the errors, so it can't redo a held change
            await self.report_errors(problems, "the code check", self._task_requesters)
        else:
            self._fix_attempts = 0

    async def report_errors(self, errors: list[str], source: str, requested_by: Iterable[str] = ()) -> Optional[str]:
        """Errors found in the project (the code check after a task, or the preview): a "Fix errors" task with
        the errors in its notes, which the coder picks up by itself. Nothing is added while a fix task is open,
        and after MAX_FIX_ATTEMPTS fixes that left errors behind the room is told once instead, so a mistake
        the model can't fix doesn't loop. Returns the new task's id."""
        errors = [" ".join(e.split())[:300] for e in errors if e.strip()][:MAX_REPORTED_ERRORS]
        plan = await self.actor.get_plan()
        if not errors or any(p["id"] == self._fix_task and p.get("status") != "done" for p in plan):
            return None
        if self._fix_attempts >= MAX_FIX_ATTEMPTS:
            if self._fix_attempts == MAX_FIX_ATTEMPTS:
                self._fix_attempts += 1
                await self._reply(f"The coder couldn't fix these errors by itself after {MAX_FIX_ATTEMPTS} tries: "
                                  f"{errors[0]}. Ask for the fix in the chat, or fix it in the editor.")
            return None
        self._fix_attempts += 1
        items = await self._plan_items()
        self._fix_task = next_task_id(items)
        fix: dict[str, Any] = {
            "id": self._fix_task, "title": FIX_TITLE,
            "status": "draft" if any(p.status == "draft" for p in items) else "todo",
            "notes": f"Found by {source}:\n" + "\n".join(f"- {e}" for e in errors),
        }
        if people := sorted(set(requested_by)):
            fix["requested_by"] = people
        await self.actor.add_plan_item(fix, COORDINATOR)
        return self._fix_task

    async def run_stopped(self, user_id: str) -> int:
        """"Run plan": retry every task the coder stopped on, as if Retry were picked on each card. A task whose
        card is gone (skipped by its default, or a restart) goes back in the queue. Tasks waiting on the coder's
        own question stay for the team to answer. Returns how many tasks were retried."""
        waiting = {s.task_id: (qid, s.parked) for qid, s in self._questions.items() if s.task_id}
        retried = 0
        if self._stuck is not None:
            self._resume()  # Run plan also retries the model after a pause
            retried = sum(1 for i in await self.actor.get_plan() if i.get("status") == "todo")
        for item in await self.actor.get_plan():
            if item.get("status") != "skipped_question":
                continue
            qid, parked = waiting.get(item["id"], (None, True))
            if not parked:
                continue
            if qid is not None:
                await self.actor.command_answer_question(qid, RETRY, user_id)
            else:
                await self.actor.update_plan_item(item["id"], {"status": "todo", "notes": "retrying"}, user_id)
            retried += 1
        if retried:
            self._wake.set()
        return retried

    def _skills(self) -> dict[str, Any]:
        """The room's enabled skills that exist on the server (a removed one is simply not offered)."""
        available = skills_library.library().skills()
        return {name: available[name] for name in sorted(self.actor.skills_enabled) if name in available}

    async def _context(self, item: dict[str, Any], can_build: bool, *, kind: Optional[str] = None,
                       skills: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        plan = await self.actor.get_plan()
        files = await self.actor.list_files()
        task = item["title"] + (f"\n{item['notes']}" if item.get("notes") else "")
        if kind == "review":
            # A review judges the code, not progress against the plan: the plan is left out
            scope = review_scope(files)
            task += f"\n\nFiles to review ({len(scope)}):\n" + "\n".join(scope[:REVIEW_MAX_FILES])
            if len(scope) > REVIEW_MAX_FILES:
                task += f"\n(and {len(scope) - REVIEW_MAX_FILES} more; review the first {REVIEW_MAX_FILES} and say so)"
        return build_context(CoderContext(
            system_prompt="\n\n".join(p for p in (
                {"review": REVIEW_SYSTEM_PROMPT, "understand": UNDERSTAND_SYSTEM_PROMPT}.get(kind or "")
                or coder_system_prompt(can_build), skills_prompt(skills or {})) if p),
            conventions=self.conventions,
            plan="" if kind == "review" else "\n".join(f"- [{p['id']}] {p['title']} ({p['status']})" for p in plan),
            current_task=f"[{item['id']}] {task}",
            repo_map="\n".join(files) or "(no files yet)",
        ))

    def _delta(self, task_id: str) -> Optional[Callable[[str], Awaitable[None]]]:
        publish = self.publish
        if publish is None:
            return None

        async def send(text: str) -> None:
            await publish(self.actor.room_id, ephemeral(
                self.actor.room_id, self.actor.sequence, "agent.text.delta", CODER,
                {"task_id": task_id, "delta": text}, datetime.now(timezone.utc).isoformat(),
            ))
        return send

    # ---- helpers ----

    async def _plan_items(self) -> list[PlanItem]:
        return [PlanItem(i["id"], i["title"], i["status"], i.get("owner_role"), i.get("notes"))
                for i in await self.actor.get_plan()]

    def _domain_role(self, user_id: str) -> Optional[DomainRole]:
        role = self.actor.domain_roles.get(user_id)
        return cast(DomainRole, role) if role in ("pm", "design", "eng") else None

    async def _spend(self, usage: Usage, user_id: str) -> None:
        if not usage.total:
            return
        await self.actor.record_tokens(usage.total, user_id)
        # The log only stores budget changes at the cap (pause/resume), so live usage goes out unstored
        if self.publish is not None:
            b = await self.actor.budget.get_status()
            await self.publish(self.actor.room_id, ephemeral(
                self.actor.room_id, self.actor.sequence, "budget.updated", user_id,
                {"tokens_used": b["tokens_used"], "runs_used": b["sandbox_runs_used"],
                 "tokens_cap": b["token_cap"], "runs_cap": b["sandbox_run_cap"]},
                datetime.now(timezone.utc).isoformat(),
            ))

    async def _reply(self, text: str) -> None:
        await self.actor.post_notice("coordinator.reply", {
            "id": str(uuid4()), "room_id": self.actor.room_id, "user_id": COORDINATOR, "text": text,
            "created_at": datetime.now(timezone.utc).isoformat(), "user": user_view(COORDINATOR, "Coordinator"),
        })

    def _later(self, delay: float, work: Callable[[], Awaitable[None]]) -> asyncio.Task:
        async def run() -> None:
            try:
                if delay:
                    await asyncio.sleep(delay)
                await work()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"Room {self.actor.room_id}: scheduled runtime work failed")

        task = asyncio.create_task(run())
        self._timers.add(task)
        task.add_done_callback(self._timers.discard)
        return task


class _Boundary:
    """The coder's pause points (architecture §3)."""

    def __init__(self, actor: RoomActor) -> None:
        self.actor = actor

    async def turn_boundary(self) -> TurnBoundary:
        merges, notes, interrupt = await self.actor.drain_inbox()
        return TurnBoundary(tuple(merges), tuple(notes), interrupt)

    async def task_boundary(self, task_id: str, summary: str) -> None:
        pass  # the runtime applies the result (status, checkpoint) once run_task returns


class _Narrated:
    """Wraps the tool executor so each call shows in the feed as tool.called / tool.result (and build.result)."""

    def __init__(self, actor: RoomActor, inner: ToolExecutor) -> None:
        self.actor = actor
        self.inner = inner

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        shown = {k: v for k, v in arguments.items() if k not in ("content", "edits")}  # summaries only
        await self.actor.post_notice("tool.called", {"tool": name, "args": shown}, CODER)
        if name in ("run_build", "run_tests") and not await self.actor.record_sandbox_run(CODER):
            result: Any = {"ok": False, "passed": False, "error": "the room is paused: its sandbox run budget is used up",
                           "errors": ["the room is paused: its sandbox run budget is used up"]}
        else:
            result = await self.inner.execute(name, arguments)
        if name == "use_skill" and isinstance(result, dict) and result.get("ok"):
            await self.actor.post_notice("skill.used", {"name": result.get("name")}, CODER)
        ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
        summary = _summary(name, result)
        await self.actor.post_notice("tool.result", {"tool": name, "summary": summary, "ok": ok}, CODER)
        if name == "run_build" and isinstance(result, dict):
            await self.actor.post_notice("build.result", {
                "passed": bool(result.get("passed")), "duration": result.get("duration_s", 0),
                "errors": list(result.get("errors") or [])[:5],
            }, CODER)
        return result


def _summary(name: str, result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)[:200]
    if not result.get("ok", True):
        return str(result.get("error", "failed"))[:200]
    if "path" in result:
        version = f" v{result['version']}" if result.get("version") else ""
        return f"{result['path']}{version}"
    if name == "list_files":
        return f"{len(result.get('files', []))} files"
    if "instructions" in result:  # use_skill: the skill's text is long; the name is enough for the feed
        return f"skill {result.get('name')}"
    if "content" in result:  # MCP tool output
        return str(result["content"])[:200]
    return str(result.get("summary") or "ok")[:200]
