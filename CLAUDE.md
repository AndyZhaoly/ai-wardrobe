# AI Mirror — Claude Code Instructions

> 把这个文件保存为新 repo 根目录的 `CLAUDE.md`。Claude Code 启动时会自动读取它。
> 第一次跑的时候,直接告诉 Claude Code:**"Start with Phase 1, Step 1."**

---

## 1. Project Context

We are a startup building an **AI smart-mirror** for fashion. The end product is a hardware mirror that helps users:

1. Get outfit recommendations and virtual try-on every morning ("智能试衣间")
2. Resell unworn clothes to second-hand platforms ("闲置变现管家")

This codebase is the **software stack** that will eventually run on / behind the mirror. For now, we're building a **production-grade web demo** to show investors and onboard early users.

The AI assistant persona inside the product is called **小镜 (Xiao Jing / "Little Mirror")**.

## 2. Where We Are Now

We have two existing codebases:

### Repo A: `ai-mirror-demo` (ours, the source)
- **Stack:** Gradio + Python + Gemini API + GSAM (Grounded-SAM) + IDM-VTON
- **What works:**
  - `vton_combined_demo.py` — full try-on flow (upload selfie → segment → recommend → VTON → save to wardrobe)
  - `poshmark_demo.py` — resale flow (upload garment → segment → generate Poshmark listing → Playwright auto-post)
  - `gsam_client.py` — calls remote GSAM service on port 8000
  - `idm_vton_client.py` — calls remote IDM-VTON service on port 8001
  - `mirror_agent.py` — Gemini agent with function calling
  - `tools/poshmark_bot.py` — Playwright automation
  - `database.json` + `database_manager.py` — toy persistence
- **What's wrong:** Gradio UI isn't shippable, persistence is a JSON file, no users, no auth, no multi-tenancy.

### Repo B: `wardrowbe` (third-party, MIT-licensed, the target shell)
- **Stack:** Next.js 14 + TypeScript + FastAPI + SQLAlchemy + PostgreSQL 15 + Redis 7 + arq + Docker Compose
- **What it gives us for free:**
  - Production-quality web app shell (auth via NextAuth/OIDC, household/multi-user, item CRUD, wear tracking, history, analytics)
  - PostgreSQL schema for wardrobe items, outfits, users, households
  - Background worker (arq) for async AI jobs
  - Generic OpenAI-compatible AI provider abstraction
  - k8s manifests, CI, release-please
- **What it doesn't have:**
  - GSAM segmentation
  - IDM-VTON virtual try-on
  - Multi-step agent with function calling (it's a single-prompt recommender)
  - Poshmark / resale flow
  - Anything resembling our planned five-layer memory

License is **MIT** — commercial use is fine. Preserve the original LICENSE file and copyright header.

## 3. Goal of This Migration

**Take the unique capabilities of Repo A and graft them into the production-grade shell of Repo B**, producing a single new repo we can iterate on.

After migration, the demo should:
- Run via `docker compose up`
- Let a user sign up, upload selfie, get outfit recommendation from agent, run VTON, save try-on result to their wardrobe
- Let a user upload a garment, get a Poshmark listing draft, optionally trigger Playwright auto-post
- Persist everything in Postgres, not `database.json`

## 4. Non-Goals (Very Important — Don't Do These)

- ❌ **Do NOT implement the five-layer memory architecture yet.** That's Phase 5+ and a separate project. For now, use Postgres only. There is a separate spec for it (see Section 7) — keep it in mind but don't build it.
- ❌ **Do NOT redesign the wardrowbe UI from scratch.** Reuse its components. Add new pages for try-on and resale, but don't touch the existing wardrobe / analytics / history pages unless asked.
- ❌ **Do NOT modify `gsam_client.py` or `idm_vton_client.py` logic.** Port them as-is, with minimal adapter changes. They talk to GPU services we run separately and they work.
- ❌ **Do NOT add features not in the migration list below.** No "while I'm at it, let me also add X."
- ❌ **Do NOT install Neo4j, Milvus, or Elasticsearch.** Postgres + Redis only for Phase 1–4.
- ❌ **Do NOT delete files from either source repo without confirmation.** When in doubt, leave it and add a TODO comment.
- ❌ **Do NOT swap Gemini for OpenAI.** The agent uses Gemini function calling specifically. Keep it. Wardrowbe's AI abstraction can stay for the "vision tagger" path, but the agent loop is Gemini-native.

## 5. Phase Plan

Work in phases. **Stop at the end of each phase and ask for human review before proceeding.** Don't run ahead.

### Phase 1: Discovery & Migration Plan (read-only)
- Clone or read both repos thoroughly
- Map every Repo A capability to its target location in Repo B
- Identify schema gaps (what tables/columns Repo B is missing for our use case)
- Produce `MIGRATION_PLAN.md` at repo root with:
  - File-by-file mapping (Repo A file → Repo B location, or "rewrite" / "drop")
  - Schema additions needed (new tables, new columns)
  - Risk list (e.g. "wardrowbe assumes one outfit = list of items, our agent emits more structured try-on results")
  - Estimated effort per item (S / M / L)
- **STOP. Wait for human review.**

### Phase 2: Skeleton Transplant
- Initialize the new repo from a clean fork of wardrowbe
- Verify base wardrowbe runs unmodified (`docker compose up`, login, upload one item, see it)
- Add a `services/gsam_client.py` and `services/vton_client.py` to the FastAPI backend, ported from Repo A
- Add env vars `GSAM_URL`, `VTON_URL`, `GEMINI_API_KEY` to `.env.example`
- Add Alembic migration(s) for new schema we'll need (mask images, try-on results, listings)
- **STOP. Wait for human review.**

### Phase 3: Agent + Try-On Flow
- Port `mirror_agent.py` to a new FastAPI module (`backend/app/agent/`)
- Implement Gemini function-calling tools as backend endpoints, not Python functions:
  - `show_recommendations`, `trigger_virtual_tryon`, `try_all_lower`, `add_to_wardrobe`
- Add a Next.js page `/tryon` with: upload selfie → chat with 小镜 → see recommendations → run VTON → save
- Reuse wardrowbe's existing item CRUD as the wardrobe backend; the agent's `add_to_wardrobe` calls the same API the manual upload uses
- **STOP. Wait for human review.**

### Phase 4: Poshmark Resale Module
- Port `tools/poshmark_bot.py` (Playwright) into the worker (arq job)
- Add Next.js page `/resell` with: upload garment → review AI-generated listing → confirm → trigger auto-post
- Listing generation goes through the agent the same way try-on does
- **STOP. Wait for human review.**

### Phase 5+ (NOT in this migration, just so you know it's coming)
- Five-layer memory architecture (Neo4j + Milvus + ES + Redis + JSON)
- Mirror UI (separate frontend, big-screen layout, voice)
- End-side inference / privacy work
- Don't build any of this now.

## 6. Tech Stack After Migration

| Layer | Tech |
|---|---|
| Frontend | Next.js 14 + TypeScript + TanStack Query + Tailwind + shadcn/ui (from wardrowbe) |
| Backend | FastAPI + SQLAlchemy async + Pydantic (from wardrowbe) |
| DB | PostgreSQL 15 (from wardrowbe) |
| Cache / Queue | Redis 7 + arq (from wardrowbe) |
| Agent | Gemini API with function calling (from ai-mirror-demo) |
| Vision (segmentation) | GSAM service on remote GPU box (from ai-mirror-demo) |
| Try-on | IDM-VTON service on remote GPU box (from ai-mirror-demo) |
| Resale automation | Playwright in arq worker (from ai-mirror-demo) |
| Deployment | Docker Compose (dev), k8s manifests (later) |

## 7. Future-Looking Spec: Five-Layer Memory (FYI only, do NOT implement now)

We have a designed-but-unbuilt memory architecture. It will eventually replace the simple Postgres + Redis combo. Phases 1–4 must be **structured to make it pluggable later**, but don't build it.

Quick summary so you know what's coming:
- **Layer 1 — Semantic:** Neo4j graph (Item, Material, Brand, StyleTag, SceneTag nodes; GOES_WITH / CONFLICTS_WITH / SUITS_SCENE edges)
- **Layer 2 — Episodic:** Milvus + Elasticsearch with RRF hybrid search over OOTD logs
- **Layer 3 — Preference state:** Redis with three namespaces (`hard_constraints`, `derived_preferences`, `item_runtime_state`)
- **Layer 4 — Procedural:** Versioned JSON playbooks for the Stylist Agent
- **Layer 5 — Working memory:** LLM context window with blackboard pattern across sub-agents
- Plus a **consolidation pipeline** (real-time + daily + weekly batches)

**Implication for current work:** When you build any agent / recommendation / persistence code in Phases 3–4, isolate the data access behind a thin interface. Don't sprinkle raw SQL queries through agent logic. Future me will swap the backing store.

## 8. Workflow Rules

- **Read before write.** Before touching a file, view it. Before changing a function, read its callers.
- **Plan before code.** For any task larger than a one-liner, write a short plan in chat, then execute. Use the TodoWrite tool aggressively.
- **One concern per commit.** Don't bundle a schema migration with a UI change with a refactor.
- **Update `PROGRESS.md` at the end of each session.** Append a dated entry: what was done, what's next, what's blocked.
- **Use TODO comments for out-of-scope cleanup.** Format: `# TODO(migration): <what> — <why deferred>`.
- **Tests:** for any new backend endpoint, add at least one pytest test. Frontend tests are nice-to-have, not required for this migration.
- **Migrations:** never edit existing Alembic migrations. Always add new ones.
- **Secrets:** never commit `.env`. Always update `.env.example` when adding a new env var.

## 9. Quality Bar

This is going to investors. The bar is:
- `docker compose up` works on a clean machine
- README has a 5-minute quickstart that actually works
- No console errors in the browser on the happy path
- No raw stack traces shown to users — friendly error messages
- Type hints on all backend functions, no `any` in TypeScript
- The GitHub README looks professional (we'll polish it together at the end)

It is **not**:
- 100% test coverage
- Perfect mobile responsiveness
- Internationalization (English-only frontend for now; agent persona 小镜 still speaks Chinese)
- Production-ready security hardening (we'll do a security pass before any real launch)

## 10. First Task

When you start, do this and only this:

> **Phase 1, Step 1: Read both codebases.**
>
> 1. List every file in `ai-mirror-demo/` (excluding `services/gsam/` and `services/idm_vton/` submodules — assume those are external services we don't modify).
> 2. List every file in `wardrowbe/backend/app/` and `wardrowbe/frontend/src/` (or wherever the source lives).
> 3. Open and skim each non-trivial file. Build a mental model of:
>    - What endpoints exist in wardrowbe today
>    - What database tables exist in wardrowbe today
>    - What our agent's function-calling tools do, signature by signature
>    - Where the Poshmark bot's automation flow plugs in
> 4. **Output a short summary** (max 1 page) of what you found, in chat. Don't write any code yet. Don't create `MIGRATION_PLAN.md` yet.
> 5. Wait for me to confirm before moving to Phase 1 Step 2.

Go.
