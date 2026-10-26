# 🚀 03 · Getting started

**← Prev** [02 · Architecture](02-architecture.md) · [📚 Docs home](README.md) · **Next →** [04 · Frontend](04-frontend.md)

---

## ✅ Prerequisites

| Tool | Version | For |
|---|---|---|
| Node.js | 20+ | `mux/web` |
| npm | 10+ | `mux/web` |
| Python | 3.11+ | `mux/server` |
| A Chromium browser | recent | WebContainers (the in-browser shell and preview) |

## 🎮 Option A: demo mode (no keys, 1 minute)

The web app ships with sample rooms, teammates, votes and files, all served from your browser.

```bash
cd mux/web
npm install
NEXT_PUBLIC_DEMO_MODE=true npm run dev
```

Then open **http://localhost:3000/room/demo-yoga**, or go to `/login` and click **Try the demo**.

> [!NOTE]
> Demo-mode data lives in `localStorage` and IndexedDB, so it survives reloads. Sign out to leave demo mode.

## 🔐 Option B: real sign-in

```bash
cd mux/web
cp .env.example .env.local
```

| Variable | What it is |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Your Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Supabase publishable key, auth only (`NEXT_PUBLIC_SUPABASE_ANON_KEY` also works) |
| `NEXT_PUBLIC_API_URL` | Backend base URL, default `http://localhost:8000` |
| `NEXT_PUBLIC_DEMO_MODE` | `true` to force demo mode (default `false`) |

In Supabase, enable the Google and GitHub providers and add `http://localhost:3000/auth/callback` as a redirect URL.

With `make server` running, signed-in rooms load from the API, and the coordinator and coder run in every room (they need the Token Factory settings in `mux/server/.env`).

## 👥 Working on one room together

Everyone in a room shares one live session. There are three ways in; the owner manages all of them from **Share** in the room's top bar:

| Way in | Who sets it up | What the teammate does | Role they get |
|---|---|---|---|
| **Link** | Owner picks "Anyone with the link" and whether link joiners can steer or only watch | Opens the link (Copy or WhatsApp) and signs in | Editor or viewer, as chosen |
| **Email invite** | Owner enters their email and a role | Clicks the link in the email, signs in with that email | The invited role, even in a private room |
| **Room ID + password** | Owner sets a password (when creating the room, or in Share) | Dashboard > **Join room**, enters the Room ID and password | Editor |

Invite emails are sent by Supabase, which needs `SUPABASE_SERVICE_ROLE_KEY` in `mux/server/.env` and the web app's `/auth/callback` in Supabase's Redirect URLs. If an email can't be sent (no key, or Supabase's hourly email limit), the invite is still saved: send the link yourself and it works the same once they sign in with that email. Supabase's built-in mailer only sends a few emails an hour; add your own SMTP server in Supabase for more.

## 🧭 Plan it with me

When you create a room or import a project with **Plan it with me** ticked (it is by default), MUX plans with you before building:

1. **Understand:** for an imported project, the coder reads it (README, package files, entry points, main pages) without changing anything and posts a summary.
2. **Ask:** the coordinator asks 2–3 multiple-choice questions, one at a time, as cards in **Decisions & plan**. Anyone in the room can answer; an unanswered card takes its default.
3. **Plan:** it drafts tasks from your idea, the summary and the answers. The owner reviews them and presses **Approve plan** to start building.

The feed shows each step. An empty plan has a **Plan it with me** button for rooms created without it. It needs an AI model (the server's or the room's own); without one, nothing starts.

While building, the plan section's buttons control the coder (editors only): **Stop the coder** interrupts the task it's on — it goes back in the plan queued — and **Resume building** (or **Start building** when it was idle) sends it back to the queued tasks. **Run plan** retries the tasks it stopped on for an answer.

### Teammates on other computers

`localhost` only works on your machine. For a quick session, `make tunnel` (needs `brew install cloudflared`) gives the web app and API public https addresses and prints the two restart commands and the Supabase redirect URL to add. For something permanent, deploy the web app and API and set `NEXT_PUBLIC_API_URL` and `WEB_APP_URL` to their addresses.

## 🔌 MCP tools for the coder

The coder can use tools from [MCP](https://modelcontextprotocol.io) servers. Every MCP call shows in the room's feed.

**For every room (whoever runs the server):** copy `mux/server/mcp.example.json` to `mux/server/mcp.json`, list servers in the same format as Claude Desktop (`command`/`args`/`env` for local servers, `url`/`headers` for remote ones), and restart `make server`. Each room's owner then turns them on in **Tools**; they're off until then.

**For one room (its owner):** **Tools** in the room's top bar → **Add a server** with a name, a public `https://` URL and an optional token. MUX connects and lists its tools. Set `ROOM_SECRETS_KEY` in `mux/server/.env` first if the server needs a token (tokens are stored encrypted and never sent back to browsers).

**Approvals:** each tool is **Auto** or **Ask first**. An Ask-first call puts an Allow / Deny card in the room; no answer before it expires counts as Deny.

**Limits:** room servers must be public `https` (set `ALLOW_PRIVATE_URLS=true` only on your own machine to try a local server); at most 10 per room; connecting gives up after 10 s, a call after 60 s; output is cut at 20,000 characters. A server that's down when a task starts is skipped for that task with a note in the feed.

## 🤖 A room's own AI model

By default every room's agents use the server's Token Factory settings (`TOKEN_FACTORY_*`, `MODEL_*`). A room's owner can use their own provider instead: **AI model** in the room's top bar → pick a provider (Nebius Token Factory, OpenAI, Anthropic, OpenRouter, Groq, Together, or any OpenAI-compatible URL), paste the API key and choose a model for each role:

| Role | Used by |
|---|---|
| Coordinator | Labels every message; pick a fast model |
| Coder | Writes the code |
| Coder when stuck | The model the coder switches to after repeated failures; pick the strongest |

**Save** sends one tiny request to each model and saves only if all answer. The key is encrypted with `ROOM_SECRETS_KEY` and never shown again; leave the key field empty later to keep it. The owner's provider bills the room's usage. **Use the server's model** goes back to the default.

If the model fails during work (wrong key, quota, rate limit), the feed says so (at most once a minute). A server with no Token Factory settings still runs agents in rooms that have their own key; other rooms have their agents off.

## 🧩 Skills

Skills are guidance packs in Claude Code's format: a folder with a `SKILL.md` (a `name` and `description` header, then instructions) and any supporting files. Put skill folders in `mux/server/skills/` (or wherever `SKILLS_PATH` points). Skills you already have in Claude Code live under `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/skills/`; copy a skill's folder across. Check each skill's license first.

In a room, the owner opens **Tools** and switches skills on (they're off by default); **Reload** picks up folders added since the server started. The coder sees each enabled skill's name and description and loads the full instructions with `use_skill` when a task needs it; the feed shows "Using skill: …".

Skills written for Claude Code's own tools (task lists, sub-agents, plan mode, git worktrees, the Bash tool) are marked **needs Claude Code**: MUX's coder can't do those parts. Guidance skills (design, testing practice, code review) work fully.

## 🪟 Windows (no WSL)

One command from the repo root handles first-run setup (backend venv + pip install, npm install) and then starts both processes in their own terminal windows:

```powershell
dev.cmd
```

| Command | What it does |
|---|---|
| `dev.cmd` | Backend venv + npm packages (first run only), then API on :8000 **and** web app on :3000 |
| `dev.cmd -Demo` | Web app only, in **demo mode** (no backend, no API keys) |
| `dev.cmd -ServerOnly` / `-WebOnly` | One process, in the current terminal |
| `dev.cmd -SetupOnly` / `-SkipSetup` | Just the installs / skip the installs |

Requirements: Python 3.11+ (tick *Add to PATH* in the installer) and Node 22 LTS in PATH. Uses `.venv\Scripts` instead of Makefile's Unix paths — `make` itself is not used on Windows.

## ⚡ Shortcut: the Makefile

From the repo root, `make help` lists everything. The usual flow:

```bash
make setup     # backend venv + npm packages, copies mux/server/.env
make demo      # web app in demo mode on :3000
make server    # API on :8000 (in another terminal)
make check     # backend tests + web type-check, lint, build
```

## 🐍 The backend

```bash
cd mux/server
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn mux.main:app --reload --port 8000   # http://localhost:8000/docs
pytest -q          # no API keys needed (fake LLM, fake sandbox)
```

Run everything from `mux/server`: the `.env` file is found relative to the current directory.

With an empty `DATABASE_URL` the room actor uses a local SQLite file. The Postgres layer (checkpoints, rewind, room files) and its 26 tests need Postgres:

```bash
docker compose up -d --wait   # Postgres 16 on localhost:5433, plus the mux_test database
pytest -q                     # now runs the DB tests too
```

<details>
<summary><b>🔑 Server environment variables</b></summary>

All of these are in `mux/server/.env.example`; `mux/config.py` reads them.

| Variable | Used by |
|---|---|
| `DATABASE_URL`, `TEST_DATABASE_URL`, `DEBUG` | Postgres (`db/`, `dbsession.py`) and tests |
| `TOKEN_FACTORY_API_KEY`, `TOKEN_FACTORY_BASE_URL` | Nemotron calls (`agents/llm.py`) |
| `MODEL_LIGHTNING`, `MODEL_SUPER`, `MODEL_ULTRA` | Model ids (check with `GET /v1/models`) |
| `SANDBOX_API_KEY`, `SANDBOX_BASE_URL`, `SANDBOX_IMAGE` | Builds and tests in Nebius Sandboxes |
| `TAVILY_API_KEY` | Conflict research and `web_search` |
| `SUPABASE_URL`, `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_AUDIENCE`, `SUPABASE_JWT_ISSUER` | JWT checks (`auth/supabase.py`) |
| `ALLOW_UNVERIFIED_TOKENS` | Local development only; never in production |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_REDIRECT_URI`, `GITHUB_TOKEN_ENCRYPTION_KEY` | GitHub export |

</details>

## 🧪 Checks to run before you push

| Where | Command | Expect |
|---|---|---|
| `mux/web` | `npx tsc --noEmit` | 0 errors |
| `mux/web` | `npm run lint` | 0 errors |
| `mux/web` | `npm run build` | passes |
| `mux/web` | `npm audit --omit=dev` | no high or critical |
| `mux/server` | `pytest -q` | all pass |
| `mux/server` | `pyright mux tests scripts` | 0 errors |

## 🩺 Troubleshooting

<details>
<summary><b>The terminal or preview says "not cross-origin isolated"</b></summary>

WebContainers need COOP/COEP headers. `next.config.ts` sends them on every page; make sure you open the app at the dev server URL directly, not inside another site's iframe, and use a Chromium-based browser.

</details>

<details>
<summary><b>Sign-in loops back to /login</b></summary>

Check that both Supabase variables are set in `.env.local`, restart `npm run dev`, and confirm the callback URL is allowed in Supabase. Errors from the provider now show on the login page.

</details>

<details>
<summary><b>"Files aren't syncing to the room server"</b></summary>

Expected until the backend files API exists. Your files are saved in this browser (IndexedDB) and are still there after a reload.

</details>

<details>
<summary><b><code>npm run gen:types</code> says there are no schemas</b></summary>

The backend's `scripts/export_schema.py` is still a stub. Once it writes JSON Schema into `mux/packages/schema/generated/`, the command generates `src/types/generated/`.

</details>

---

**← Prev** [02 · Architecture](02-architecture.md) · **Next →** [04 · Frontend](04-frontend.md)
