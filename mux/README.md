# MUX

A multiplayer coding agent: a whole team steers one agent at the same time. Built for the Nebius x NVIDIA Global AI Hackathon 2026.

The name comes from an 8:1 multiplexer: up to 8 people steering, one agent building.

Design docs live one level up: [`../source-of-truth/prd.md`](../source-of-truth/prd.md), [`../session-log/`](../session-log/), [`../source-of-truth/design-theme.md`](../source-of-truth/design-theme.md).

## Repository layout

Setup and run instructions: [`../docs/03-getting-started.md`](../docs/03-getting-started.md). From the repo root, `make help` lists the common commands.

```
mux/
├── server/                      Python backend (FastAPI), runs on a Nebius CPU VM
│   ├── pyproject.toml
│   ├── .env.example
│   ├── scripts/export_schema.py Pydantic events -> packages/schema
│   ├── tests/
│   └── mux/
│       ├── main.py              App factory, routers, startup
│       ├── config.py            Environment settings
│       ├── api/                 HTTP + WebSocket routes (thin; hand off to the room actor)
│       │   ├── rooms.py         Rooms, sharing, memberships
│       │   ├── commands.py      Steer, vote, approve plan, answer question, rewind, end session
│       │   ├── files.py         Manual code editing with soft locks
│       │   ├── export.py        Connect GitHub + export
│       │   └── ws.py            Event stream (?since=seq) + presence
│       ├── auth/                Supabase JWT check, owner/editor/viewer rules
│       ├── rooms/               One in-memory actor per room
│       │   ├── actor.py         Serializes every change for the room
│       │   ├── registry.py      Start, stop, rehydrate actors
│       │   ├── inbox.py         Messages waiting for the coder's next turn boundary
│       │   ├── plan.py          Plan model + validation
│       │   ├── locks.py         One person edits a file at a time
│       │   ├── budget.py        Token and build accounting
│       │   ├── presence.py      Ephemeral presence
│       │   └── sitting.py       End-of-sitting detection
│       ├── agents/
│       │   ├── llm.py           Token Factory client, model choice, usage, caching-friendly prompts
│       │   ├── coordinator/     Decides WHAT gets built (Lightning; create_plan on Super)
│       │   │   ├── agent.py     One message at a time per room
│       │   │   ├── schema.py    classify, create_plan, add_plan_item, open_conflict, research_conflict, reply
│       │   │   ├── planner.py   create_plan, add_plan_item
│       │   │   ├── conflicts.py Conflict cards, Tavily evidence, votes
│       │   │   └── prompts.py
│       │   └── coder/           Decides HOW to build the current task (Super, Ultra on escalation)
│       │       ├── loop.py      Hand-rolled agent loop
│       │       ├── context.py   Fresh context per task
│       │       ├── compaction.py Shrinks used tool results (token saving)
│       │       ├── escalation.py Turn limits, loop detection, Ultra after 2 failed builds
│       │       ├── prompts.py
│       │       └── tools/       One file per tool group
│       │           ├── files.py   read_file, write_file, edit_file, list_files, delete_file
│       │           ├── build.py   run_build, run_tests
│       │           ├── search.py  web_search (Tavily)
│       │           ├── plan.py    update_plan (narrow)
│       │           ├── ask.py     ask_room
│       │           └── finish.py  finish_task
│       ├── files/               Content-addressed store, manifests, repo map
│       ├── checkpoints/         Checkpoint per task, rewind
│       ├── memory/              Rolling task log, day log, pinned facts
│       ├── sandbox/             Docker and Nebius sandbox clients, runner, error trimming
│       ├── integrations/        Tavily, GitHub
│       ├── events/              Pydantic event models, Postgres log, fan-out bus
│       ├── db/                  Engine, tables
│       └── replay/              Fake LLM + fake sandbox so the UI can be built first
├── web/                         Next.js + TypeScript frontend, deployed on Vercel
│   ├── next.config.ts           COOP/COEP headers for WebContainers
│   └── src/
│       ├── app/                 Dashboard, login, room page
│       ├── components/
│       │   ├── room/            Top bar, presence, budget meter, share dialog
│       │   ├── feed/            Feed, label chips, composer, conflict banner
│       │   ├── center/          Preview (WebContainer), code editor, file tree
│       │   ├── side/            Conflict card, question card, plan, plan approval
│       │   └── timeline/        Checkpoint scrubber
│       ├── lib/                 REST client, resumable socket, event reducer, Supabase, WebContainer
│       ├── styles/theme.css     GitHub Dark tokens
│       └── types/               Generated from packages/schema
├── packages/schema/             JSON Schema generated from the Python event models
├── templates/fullstack-starter/ What the coder builds on: React + Vite + Tailwind, Hono, SQLite
│   ├── client/  server/
│   ├── CONVENTIONS.md           Goes into the coder prompt
│   └── approved-packages.json   The only packages the coder may use
├── evals/                       Coordinator accuracy, build success, tokens vs naive agent
└── infra/                       Caddyfile, VM setup, sandbox starter image
```

## Who owns what

| Person | Folders |
|---|---|
| P1: realtime frontend | `web/` |
| P2: agent core | `server/mux/agents/`, `server/mux/memory/`, `evals/` |
| P3: infra | `server/mux/sandbox/`, `server/mux/checkpoints/`, `server/mux/files/`, `server/mux/integrations/github.py`, `infra/`, `templates/` |
| P4: product, design, demo | `server/mux/integrations/tavily.py`, `server/mux/agents/coordinator/conflicts.py`, UX review of `web/` |

Shared: `server/mux/rooms/`, `server/mux/api/`, `server/mux/events/`, `packages/schema/`.
