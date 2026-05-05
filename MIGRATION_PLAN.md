# Migration Plan: ai-mirror-demo → ai-wardrobe

_Phase 1 output. Generated after reading both codebases in full._

---

## 1. File-by-File Mapping

### Repo A → Repo B

| Repo A File | Destination | Action | Effort |
|---|---|---|---|
| `gsam_client.py` | `backend/app/services/gsam_client.py` | Port as-is | S |
| `idm_vton_client.py` | `backend/app/services/vton_client.py` | Port as-is | S |
| `mirror_agent.py` | `backend/app/agent/mirror_agent.py` | Port + adapt to FastAPI context | M |
| `recommendations.py` | `backend/app/services/garment_analyzer.py` | Port VLM analysis; replace SAMPLE_CLOTHES_DB with Postgres query | M |
| `database_manager.py` | _drop_ | Replaced by SQLAlchemy models + new Alembic migrations | — |
| `database.json` | _drop_ | Data migrated to Postgres on first run (or seeded) | S |
| `vton_combined_demo.py` (tool handlers) | `backend/app/agent/tools/tryon_tools.py` | Extract 4 tool functions, make them call FastAPI services | M |
| `vton_combined_demo.py` (Gradio UI) | `frontend/src/app/dashboard/tryon/page.tsx` | New Next.js page, reuse existing components | L |
| `poshmark_demo.py` (tool handlers) | `backend/app/agent/tools/resale_tools.py` | Extract 4 tool functions | M |
| `poshmark_demo.py` (Gradio UI) | `frontend/src/app/dashboard/resell/page.tsx` | New Next.js page | L |
| `tools/poshmark_bot.py` | `backend/app/workers/poshmark_job.py` | Wrap as arq job; keep Playwright logic intact | M |
| `tools/gemini_analyzer.py` | `backend/app/services/gemini_analyzer.py` | Port as helper for agent | S |
| `tools/pricing_tool.py` | `backend/app/services/pricing_service.py` | Port pricing logic | S |
| `poshmark_category_tree.json` | `backend/app/data/poshmark_category_tree.json` | Copy as static data file | S |
| `app.py` | _drop_ | Old monolith Gradio app, superseded | — |
| `idm_vton_demo.py` | _drop_ | Standalone demo, superseded | — |
| `workflow.py` | _drop_ | LangGraph workflow, superseded by agent module | — |
| `mock_apis.py` | _drop_ | No longer needed | — |
| `segment_service.py` | _drop_ | Local SAM service, superseded by remote GSAM | — |
| `demo_garments/` | `backend/app/data/demo_garments/` + DB seed | Copy images; seed into `clothing_items` with `is_demo=true` | S |
| `recommendations/clothes/` | _drop_ | Will use demo_garments as canonical source | — |

---

## 2. Schema Additions (New Alembic Migrations)

### Migration A: Extend `clothing_items`

```sql
ALTER TABLE clothing_items ADD COLUMN mask_image_path VARCHAR;     -- GSAM segmentation result
ALTER TABLE clothing_items ADD COLUMN is_demo BOOLEAN DEFAULT FALSE; -- seeded demo garments
ALTER TABLE clothing_items ADD COLUMN source VARCHAR DEFAULT 'upload'; -- 'upload' | 'demo' | 'tryon'
```

### Migration B: New table `tryon_sessions`

```sql
CREATE TABLE tryon_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    person_image_path VARCHAR NOT NULL,        -- uploaded selfie
    garment_item_id UUID REFERENCES clothing_items(id),  -- selected demo garment
    result_image_path VARCHAR,                 -- IDM-VTON output
    clothing_category VARCHAR,                 -- 'upper_body' | 'lower_body' | 'dresses'
    prompt TEXT,                               -- CLIP prompt sent to VTON
    status VARCHAR DEFAULT 'pending',          -- 'pending' | 'processing' | 'done' | 'error'
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
```

### Migration C: New table `resale_listings`

```sql
CREATE TABLE resale_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id UUID REFERENCES clothing_items(id),
    garment_image_path VARCHAR NOT NULL,       -- original upload
    cropped_image_path VARCHAR,                -- GSAM upper-body crop
    listing_title VARCHAR(50),
    listing_description TEXT,
    original_price_cny INTEGER,
    listing_price_usd INTEGER,
    poshmark_category_path JSONB,              -- ["Women", "Jackets & Coats"]
    status VARCHAR DEFAULT 'draft',            -- 'draft' | 'posted' | 'error'
    poshmark_listing_id VARCHAR,               -- from Poshmark after posting
    posted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Migration D: Add `GEMINI_API_KEY` to `user_preferences`

```sql
ALTER TABLE user_preferences ADD COLUMN gemini_api_key_override VARCHAR;
```
_(Global key from env; per-user override for future multi-tenant use)_

---

## 3. New Environment Variables (`.env.example` additions)

```bash
# GPU service URLs (can be localhost via SSH tunnel or direct remote)
GSAM_URL=http://localhost:8000
VTON_URL=http://localhost:8001

# Gemini (agent loop — separate from wardrowbe's OpenAI-compatible AI)
GEMINI_API_KEY=your_gemini_api_key

# Poshmark worker
PLAYWRIGHT_BROWSER_DATA_PATH=/data/wardrobe/poshmark_browser_data
```

---

## 4. New Backend Modules

```
backend/app/
├── agent/
│   ├── __init__.py
│   ├── mirror_agent.py          # Ported from Repo A, adapted for FastAPI/async
│   ├── tools/
│   │   ├── tryon_tools.py       # show_recommendations, trigger_virtual_tryon, try_all_lower, add_to_wardrobe
│   │   └── resale_tools.py      # identify_item, get_resale_price, generate_listing, post_to_poshmark
│   └── router.py                # POST /agent/chat (SSE streaming)
├── services/
│   ├── gsam_client.py           # Ported as-is
│   ├── vton_client.py           # Ported as-is
│   ├── garment_analyzer.py      # VLM image analysis (from recommendations.py)
│   ├── pricing_service.py       # CNY→USD, price range
│   └── gemini_analyzer.py       # Gemini VLM helper
├── workers/
│   └── poshmark_job.py          # arq job wrapping poshmark_bot.py
├── data/
│   ├── demo_garments/           # Seeded clothing images
│   └── poshmark_category_tree.json
└── api/
    ├── tryon.py                 # GET/POST /tryon, GET /tryon/{session_id}
    └── resale.py                # GET/POST /resale, GET /resale/{listing_id}
```

---

## 5. New Frontend Pages

```
frontend/src/app/dashboard/
├── tryon/
│   └── page.tsx     # Upload selfie → chat with 小镜 → VTON result → save
└── resell/
    └── page.tsx     # Upload garment → listing draft → confirm → post
```

Sidebar nav needs 2 new entries: "试衣间" and "二手出售".

---

## 6. Risk List

| Risk | Severity | Notes |
|---|---|---|
| **Gemini vs OpenAI-compatible abstraction** | High | Wardrowbe's `ai_service.py` uses OpenAI SDK. Our agent loop uses Gemini function calling natively (tool_calls format). They cannot share the same abstraction. Keep them parallel: wardrowbe's `AIService` for item tagging; `GeminiAgent` for conversation. |
| **Playwright in Docker** | High | arq worker container needs chromium + playwright installed. Must add to `backend/Dockerfile`: `RUN playwright install chromium --with-deps`. Increases image size ~300MB. |
| **GSAM/VTON remote GPU services** | Medium | These aren't part of `docker compose up`. Need SSH tunnel or direct URL. Must document clearly in README. `docker compose up` will work but try-on silently degrades if services unreachable (clients have `.available` flag). |
| **try-on outfit model mismatch** | Medium | Wardrowbe's `outfits` table = list of items from user's wardrobe. Our try-on result = a generated image + a demo garment. We cannot reuse `outfits` directly. Use the new `tryon_sessions` table instead and link to wardrobe via `add_to_wardrobe` → creates a real `clothing_item` entry. |
| **Poshmark browser login** | Medium | First run requires manual browser login. In Docker, must mount `poshmark_browser_data` as a volume and pre-login outside the container, or expose a "login" endpoint that launches a visible browser session (complex). For demo, mount as host volume + pre-login. |
| **demo_garments VLM analysis** | Low | Currently runs at Gradio startup, caches to JSON. In new stack, run once as a seed script or arq job. Cache in Postgres `clothing_items` rows with `is_demo=true`. |
| **Image path portability** | Low | `database_manager.py` stores absolute paths. In Docker, paths must be inside the `wardrobe_data` volume. All new code must store relative-to-storage-root paths and resolve at read time. |

---

## 7. What Stays Untouched in Wardrowbe

Per non-goals — do NOT touch:
- All existing wardrobe/analytics/history/family pages
- `ai_service.py` (wardrowbe's item tagging path)
- All existing Alembic migrations
- Auth system (NextAuth + OIDC)
- Notification / scheduling / learning systems

---

## 8. Phased Effort Estimate

| Phase | Work Items | Estimate |
|---|---|---|
| **2 — Skeleton** | Port gsam_client + vton_client, add .env vars, write 3 Alembic migrations, add poshmark_browser_data volume to compose | 1 day |
| **3 — Agent + Try-On** | Port mirror_agent, build tryon_tools, add /agent/chat SSE endpoint, build /dashboard/tryon page | 3–4 days |
| **4 — Resale** | Port poshmark_bot as arq job, build resale_tools, add /dashboard/resell page | 2 days |
| **Polish** | Docker image (playwright), README quickstart, error messages | 1 day |

**Total: ~7–8 days of focused work.**
