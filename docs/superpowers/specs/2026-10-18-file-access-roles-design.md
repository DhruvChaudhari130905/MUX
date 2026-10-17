# Coder roles and folder access — design

## Why

In a room, several coders work in one project at the same time. The owner wants each coder to stay in
their own area: a backend engineer changes backend files, a frontend developer changes frontend files.
A coder can still change something outside their area, but only after someone responsible for that
area (or the owner) says yes. This guards against accidental or unwanted edits to someone else's part
of the code. It applies to edits a coder makes by hand **and** to edits the AI agent makes for them.

## Goals

- The owner defines **coder roles** for the room (e.g. "Backend engineer", "Frontend developer") and
  assigns **folders** to each role.
- The owner gives each editor a coder role, and can also assign extra folders to one person directly.
- Every coder can **read** every file. Only **changes** (save, create, delete, rename) are restricted.
- A change outside the person's area becomes a **change request** that shows the exact change.
  The first approval from the owner **or** any coder whose area contains the file applies it;
  a rejection discards it.
- An approval covers **that one change only**. It never grants ongoing access.
- Files outside every assigned folder are **owner-approved**: a change to them needs the owner's yes.
- The agent respects the same rules when its work comes from one person's request.

## Non-goals

- No change to viewers: they stay read-only and cannot send change requests.
- No hiding files. Read access is never restricted.
- No change to the existing PM / design / eng tags, which weight votes. Coder roles are separate.
- No plan-tier gating of this feature (can be added later, see pricing page).

## Anti-goals (would make it a failure even if it works)

- A restricted coder can get a change in through a side door: the agent, file delete, rename, upload,
  import, rewind, or the terminal/shell.
- Approvers drown in requests, or a request is applied to a file that has changed since, overwriting
  someone's newer work.
- The agent stalls the whole task while waiting on one approval.
- Rooms that don't use roles behave any differently from today.

## Rules in detail

**Who is affected.** The owner is never restricted. An editor with **no** coder role and **no**
personal folders is unrestricted (today's behaviour), so rooms that never set this up don't change.
Once an editor has a role or personal folders, the rules apply to them.

**A person's area** = the folders of their role ∪ their personal folders. A folder covers itself and
everything under it (`server/` covers `server/mux/api/rooms.py`). Paths are matched after the same
normalisation `check_path` already does.

**Owners of a file** (who can approve a change to it) = the room owner plus every coder whose area
contains the file. If no coder's area contains it, only the owner.

**A change** is checked against every path it touches. A rename touches both old and new paths.

**The agent.**
- When the agent's current task came from **one person** (a plan item created from their single
  message, or one person's message merged into the running task), the agent's writes are checked
  against that person's area.
- An agent write outside that area is **held as a change request** on that person's behalf. The agent
  is told the write is pending approval (a tool result, not an error), and carries on with the rest of
  the task. It must not retry the held write or route around it.
- When the task has **no single requester** (several people's messages merged in, a room vote or
  conflict resolution decided it, the kickoff plan, or the owner approved the plan), the agent may
  change any file. The room already agreed on that work.

**Who is notified.** A new request notifies the owner and every coder who can approve it. A request
that touches files in several areas can be approved by the owner, or by a coder whose area contains
**all** of its files.

**Approving.** An approval applies the stored change only if every touched file is still at the version
the change was based on. If a file moved on, the request is marked **stale** and the requester is
told to redo it. Approvers see the diff before deciding. The requester can withdraw a pending request.

**Removing access.** If the owner changes someone's role or folders, their pending requests stay and
are judged under the new rules when someone approves them.

## Where it lives in the code (decided)

- **Server, room actor** (`server/mux/rooms/actor.py`): coder roles, role folders, per-person role and
  folders, and change requests are new room state, recorded as events so they replay like members and
  sharing do today. A single `can_change(user_id, path)` check is the only source of truth.
- **Server, file writes**: every write path — `save_file`, `delete_file`, rename/upload/import,
  rewind — goes through the check. `RoomFileTools` (agent) goes through it with the task's requester.
- **Server, runtime** (`server/mux/rooms/runtime.py`): plan items get an optional `requested_by`
  (set when a task is created from one person's message), and the running task tracks whether more
  than one person has merged into it.
- **Web**: the owner manages roles and folders from the room's existing sharing / members area
  (`components/room/ShareDialog.tsx`), picking folders from the file tree. Change requests show up in
  room notifications (`components/room/Notifications.tsx`) with the diff and Approve / Reject. The file
  tree marks folders outside your area, and saving there says "This will be sent for approval".

## Left to the builder

- Exact event names, API routes and response shapes, following the existing patterns.
- How the diff is rendered and how the folder picker looks, using existing components.
- Whether the shell/terminal enforces the rules or is simply closed to restricted coders (it must not
  be a way around them; closing it for restricted coders is acceptable).
- Tests: the rules above are the test list. Every write path needs a "restricted coder is held" test.

## Calls I made (not decided by the room owner)

- Coder role names are free text the owner types, not a fixed list.
- Editors without any role or folders are unrestricted.
- Requests that went stale are not auto-retried.
- The agent continues its task while a write waits for approval.
- A request spanning several areas needs the owner, or one coder who covers all of its files.
