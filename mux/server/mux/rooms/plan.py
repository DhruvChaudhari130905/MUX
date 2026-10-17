"""Plan model ([{id, title, status, owner_role?}]) and validation of every plan change, whichever agent makes it."""

from __future__ import annotations

from enum import Enum
from typing import List, Dict, Any, Optional, TypedDict
import asyncio
import threading


class PlanStatus(str, Enum):
    """Valid plan item statuses."""
    DRAFT = "draft"
    TODO = "todo"
    DOING = "doing"
    DONE = "done"
    SKIPPED_CONFLICT = "skipped_conflict"
    SKIPPED_QUESTION = "skipped_question"


class PlanItem(TypedDict, total=False):
    """Type-safe plan item schema."""
    id: str
    title: str
    status: str  # draft, todo, doing, done, skipped_conflict, skipped_question
    owner_role: Optional[str]  # pm, design, eng
    requested_by: Optional[List[str]]  # people whose requests shaped the task (their folders bound the coder)
    notes: Optional[str]
    merged_notes: Optional[List[str]]


class Plan:
    """Plan model and validation. Async-safe with internal lock."""

    # Valid statuses and roles as class constants for discoverability
    VALID_STATUSES = frozenset({s.value for s in PlanStatus})
    VALID_ROLES = frozenset({"pm", "design", "eng"})

    def __init__(self, items: Optional[List[Dict[str, Any]]] = None) -> None:
        self._items: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._sync_lock = threading.RLock()
        if items:
            self._items = [self._validate_item(item) for item in items]

    def _validate_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a plan item dict and return a normalized copy.
        Raises ValueError if validation fails.
        """
        # Make a copy to avoid mutating the original
        validated = item.copy()

        # Required fields
        if "id" not in validated or not isinstance(validated["id"], str):
            raise ValueError("Plan item must have a string 'id'")
        if "title" not in validated or not isinstance(validated["title"], str):
            raise ValueError("Plan item must have a string 'title'")
        if "status" not in validated:
            raise ValueError("Plan item must have a 'status'")

        # Validate status
        if validated["status"] not in self.VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{validated['status']}'. Must be one of {sorted(self.VALID_STATUSES)}"
            )

        # Optional owner_role: must be one of 'pm', 'design', 'eng' if present
        if "owner_role" in validated and validated["owner_role"] is not None:
            if validated["owner_role"] not in self.VALID_ROLES:
                raise ValueError(
                    f"Invalid owner_role '{validated['owner_role']}'. Must be 'pm', 'design', or 'eng'"
                )

        people = validated.get("requested_by")
        if people is not None and not (isinstance(people, list) and all(isinstance(p, str) for p in people)):
            raise ValueError("Plan item 'requested_by' must be a list of user ids if provided")

        # Optional notes: must be string if present
        if "notes" in validated and validated["notes"] is not None:
            if not isinstance(validated["notes"], str):
                raise ValueError("Plan item 'notes' must be a string if provided")

        # Optional merged_notes: must be list of strings if present
        if "merged_notes" in validated and validated["merged_notes"] is not None:
            if not isinstance(validated["merged_notes"], list):
                raise ValueError("Plan item 'merged_notes' must be a list if provided")
            for note in validated["merged_notes"]:
                if not isinstance(note, str):
                    raise ValueError("Plan item 'merged_notes' must be a list of strings")

        return validated

    async def _ensure_id_unique(self, item_id: str, exclude_index: Optional[int] = None) -> None:
        """Ensure no other item in the plan has the given id (internal, no lock).
        Raises ValueError if duplicate found.
        """
        for i, item in enumerate(self._items):
            if i == exclude_index:
                continue
            if item.get("id") == item_id:
                raise ValueError(f"Plan item id '{item_id}' is not unique")

    def _validate_item_no_lock(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a plan item dict and return a normalized copy (internal, no lock).
        Raises ValueError if validation fails.
        """
        return self._validate_item(item)

    async def draft(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Replace the plan with new items (used for plan.drafted event).
        Returns the new list of items.
        """
        async with self._lock:
            validated_items = [self._validate_item(item) for item in items]
            # Check for duplicate ids across the new list
            seen_ids = set()
            for item in validated_items:
                if item["id"] in seen_ids:
                    raise ValueError(f"Duplicate plan item id '{item['id']}' in drafted plan")
                seen_ids.add(item["id"])
            self._items = validated_items
            return self._items.copy()

    async def replace(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Replace the entire plan with new items (used for plan.edited event).
        Returns the new list of items.
        """
        return await self.draft(items)  # same validation as draft

    async def edit(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Backward-compatible alias for replace()."""
        return await self.replace(items)

    async def update_items(self, changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Bulk update multiple items by id.
        Each entry in changes must have 'id' key.
        Returns the updated plan items list.
        """
        async with self._lock:
            for change in changes:
                if "id" not in change:
                    raise ValueError("Each change must include 'id'")
                await self._update_item_no_lock(change["id"], {k: v for k, v in change.items() if k != "id"})
            return await self._get_items_no_lock()

    async def _update_item_no_lock(self, item_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Internal update_item without lock acquisition."""
        if "id" in changes:
            raise ValueError("Plan item 'id' is immutable; use remove_item + add_item to change id")

        for i, item in enumerate(self._items):
            if item["id"] == item_id:
                updated = item.copy()
                updated.update(changes)
                validated = self._validate_item_no_lock(updated)
                self._items[i] = validated
                return validated.copy()
        raise ValueError(f"Plan item with id '{item_id}' not found")

    async def _get_items_no_lock(self) -> List[Dict[str, Any]]:
        """Internal get_items without lock acquisition."""
        return [item.copy() for item in self._items]

    async def add_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new plan item (used for plan.item_added event).
        Returns the added item.
        """
        async with self._lock:
            validated = self._validate_item_no_lock(item)
            await self._ensure_id_unique(validated["id"])
            self._items.append(validated)
            return validated.copy()

    async def update_item(self, item_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing plan item by id (used for plan.item_updated event).
        Returns the updated item.
        Raises ValueError if item not found or if 'id' is in changes (id is immutable).
        """
        async with self._lock:
            return await self._update_item_no_lock(item_id, changes)

    async def remove_item(self, item_id: str) -> Dict[str, Any]:
        """Remove a plan item by id (used for plan.item_removed event).
        Returns the removed item.
        Raises ValueError if item not found.
        """
        async with self._lock:
            for i, item in enumerate(self._items):
                if item["id"] == item_id:
                    removed = self._items.pop(i)
                    return removed.copy()
            raise ValueError(f"Plan item with id '{item_id}' not found")

    async def move_item(self, item_id: str, new_index: int) -> List[Dict[str, Any]]:
        """Move a plan item to a new position (used for drag-and-drop reordering).
        Returns the updated plan items list.
        Raises ValueError if item not found or index out of bounds.
        """
        async with self._lock:
            # Find the item
            item = None
            old_index = -1
            for i, it in enumerate(self._items):
                if it["id"] == item_id:
                    item = it
                    old_index = i
                    break
            if item is None:
                raise ValueError(f"Plan item with id '{item_id}' not found")

            # Clamp new_index to valid range
            new_index = max(0, min(new_index, len(self._items) - 1))
            if new_index == old_index:
                return await self._get_items_no_lock()

            # Remove and reinsert at new position
            self._items.pop(old_index)
            self._items.insert(new_index, item)
            return await self._get_items_no_lock()

    async def complete_item(self, item_id: str) -> Dict[str, Any]:
        """Mark a plan item as completed (status 'done') (used for plan.item_completed event).
        Returns the updated item.
        Raises ValueError if item not found.
        """
        async with self._lock:
            return await self._update_item_no_lock(item_id, {"status": "done"})

    async def approve_draft_items(self) -> List[Dict[str, Any]]:
        """Change status of all draft items to 'todo' (used when plan is approved).
        Returns the list of items after approval.
        """
        async with self._lock:
            for item in self._items:
                if item["status"] == "draft":
                    item["status"] = "todo"
            # Re-validate all items after status change
            for item in self._items:
                self._validate_item_no_lock(item)
            return await self._get_items_no_lock()

    async def approve_items(self, item_ids: List[str]) -> List[Dict[str, Any]]:
        """Approve specific draft items by changing their status to 'todo'.
        Returns the list of items after approval.
        """
        async with self._lock:
            for item in self._items:
                if item["id"] in item_ids and item["status"] == "draft":
                    item["status"] = "todo"
            # Re-validate all items after status change
            for item in self._items:
                self._validate_item_no_lock(item)
            return await self._get_items_no_lock()

    async def get_items(self) -> List[Dict[str, Any]]:
        """Return a shallow copy of the plan items list."""
        async with self._lock:
            return await self._get_items_no_lock()

    async def get_items_view(self) -> tuple:
        """Return an immutable tuple view of items (no copy, for iteration)."""
        async with self._lock:
            return tuple(self._items)

    def __len__(self) -> int:
        with self._sync_lock:
            return len(self._items)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        with self._sync_lock:
            return self._items[index].copy()

    def __eq__(self, other: object) -> bool:
        with self._sync_lock:
            if not isinstance(other, Plan):
                return NotImplemented
            return self._items == other._items

    def __repr__(self) -> str:
        with self._sync_lock:
            return f"Plan(items={self._items!r})"
