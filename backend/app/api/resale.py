"""
Resale API endpoints.

POST /resale/garment          — upload garment photo, GSAM crop, create ResaleListing
GET  /resale/{listing_id}     — get listing status + generated text
POST /resale/chat             — SSE chat with 小镜 (analyze → price → generate → post)
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Annotated

from arq import create_pool
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.mirror_agent import MirrorAgent
from app.agent.tools.resale_tools import (
    handle_analyze_garment,
    handle_generate_poshmark_listing,
    handle_post_to_poshmark,
)
from app.config import get_settings
from app.database import get_db
from app.models.tryon import ResaleListing
from app.services.gsam_client import GSAMClient
from app.utils.auth import CurrentUser
from app.workers.settings import get_redis_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/resale", tags=["Resale"])

_gsam = GSAMClient(check_health=False)

RESALE_SYSTEM_PROMPT = """
你是专业的二手变现顾问，名叫"小镜"，帮助主人将闲置衣物发布到 Poshmark 出售。

【流程】
1. 主人上传衣物照片后，你先调用 analyze_garment 识别品牌和估价
2. 告诉主人识别结果（品牌、成色、建议售价），询问是否需要调整
3. 确认后调用 generate_poshmark_listing 生成英文文案
4. 给主人看文案，等待确认
5. 主人确认发布后调用 post_to_poshmark

【风格】忠诚、专业、简洁。用中文回复，文案用英文。称主人为"主人"。
"""

RESALE_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_garment",
            "description": "用 Gemini 识别衣物品牌、成色、估价。上传照片后第一步调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_poshmark_listing",
            "description": "生成 Poshmark 英文 listing 文案。在主人确认估价后调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "post_to_poshmark",
            "description": "提交 Poshmark 自动发布任务。主人明确确认发布后才能调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ── schemas ───────────────────────────────────────────────────────────────────

class GarmentUploadResponse(BaseModel):
    listing_id: str
    cropped_image_url: str | None = None


class ListingResponse(BaseModel):
    listing_id: str
    status: str
    listing_title: str | None = None
    listing_description: str | None = None
    listing_price_usd: int | None = None
    original_price_cny: int | None = None


class ChatRequest(BaseModel):
    listing_id: str
    message: str
    history: list[dict] = []


# ── helpers ───────────────────────────────────────────────────────────────────

def _save_bytes(data: bytes, rel_path: str) -> str:
    abs_path = Path(settings.storage_path) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(data)
    return rel_path


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/garment", response_model=GarmentUploadResponse)
async def upload_garment(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
) -> GarmentUploadResponse:
    """Upload garment photo → GSAM crop → create ResaleListing."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an image")

    listing_id = uuid.uuid4()
    contents = await file.read()

    rel_garment = f"resale/garments/{listing_id}.jpg"
    _save_bytes(contents, rel_garment)
    abs_garment = str(Path(settings.storage_path) / rel_garment)

    cropped_rel: str | None = None
    if _gsam.available:
        try:
            upper_imgs, _ = _gsam.extract_upper_body(abs_garment, white_background=True)
            if upper_imgs:
                from PIL import Image
                img: Image.Image = upper_imgs[0]
                rel = f"resale/cropped/{listing_id}.png"
                abs_out = Path(settings.storage_path) / rel
                abs_out.parent.mkdir(parents=True, exist_ok=True)
                img.save(str(abs_out))
                cropped_rel = rel
        except Exception as exc:
            logger.warning("GSAM crop failed for listing %s: %s", listing_id, exc)

    listing = ResaleListing(
        id=listing_id,
        user_id=current_user.id,
        garment_image_path=rel_garment,
        cropped_image_path=cropped_rel,
        status="draft",
    )
    db.add(listing)
    await db.commit()

    cropped_url = (
        f"/api/v1/resale/image/{cropped_rel}" if cropped_rel
        else f"/api/v1/resale/image/{rel_garment}"
    )
    return GarmentUploadResponse(listing_id=str(listing_id), cropped_image_url=cropped_url)


@router.get("/image/{path:path}")
async def get_resale_image(path: str, current_user: CurrentUser):
    """Serve resale images."""
    from fastapi.responses import FileResponse
    abs_path = Path(settings.storage_path) / path
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(abs_path))


@router.get("/{listing_id}", response_model=ListingResponse)
async def get_listing(
    listing_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ListingResponse:
    listing = await db.get(ResaleListing, listing_id)
    if not listing or listing.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Listing not found")
    return ListingResponse(
        listing_id=str(listing.id),
        status=listing.status,
        listing_title=listing.listing_title,
        listing_description=listing.listing_description,
        listing_price_usd=listing.listing_price_usd,
        original_price_cny=listing.original_price_cny,
    )


@router.post("/chat")
async def chat(
    request: ChatRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """SSE chat with 小镜 for the resale flow."""
    listing_uuid = uuid.UUID(request.listing_id)
    listing = await db.get(ResaleListing, listing_uuid)
    if not listing or listing.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Listing not found")

    garment_path = str(
        Path(settings.storage_path) / (listing.cropped_image_path or listing.garment_image_path)
    )

    # item_info cache (populated after analyze_garment runs)
    _item_info: dict = {}

    async def _analyze() -> dict:
        result = await handle_analyze_garment(db, listing_uuid, garment_path)
        _item_info.update(result)
        return result

    async def _generate() -> dict:
        return await handle_generate_poshmark_listing(db, listing_uuid, _item_info)

    async def _post() -> dict:
        result = await handle_post_to_poshmark(db, listing_uuid)
        if result.get("status") == "queued":
            try:
                redis = await create_pool(get_redis_settings())
                await redis.enqueue_job("run_poshmark_job", request.listing_id)
                await redis.aclose()
            except Exception as exc:
                logger.warning("Failed to queue poshmark job: %s", exc)
        return result

    tool_handlers = {
        "analyze_garment": _analyze,
        "generate_poshmark_listing": _generate,
        "post_to_poshmark": _post,
    }

    # Use MirrorAgent with custom tool defs for the resale system prompt
    from app.agent.mirror_agent import TOOL_DEFS as _DEFAULT_DEFS
    from app.agent.mirror_agent import MirrorAgent

    agent = MirrorAgent()
    # Temporarily override model's tool defs and system prompt for resale context
    agent._resale_mode = True

    async def _stream():
        # Inline: re-implement stream_chat with resale prompt + tools
        import json as _json
        from openai import APIError, RateLimitError
        if not agent._available():
            yield f"data: {_json.dumps({'type':'text','delta':'（请配置 GEMINI_API_KEY）'})}\n\n"
            yield f"data: {_json.dumps({'type':'done','messages':request.history})}\n\n"
            return

        full_messages = [{"role": "system", "content": RESALE_SYSTEM_PROMPT}] + request.history

        try:
            for _ in range(6):
                resp = agent._client.chat.completions.create(
                    model=agent.model,
                    messages=full_messages,
                    tools=RESALE_TOOL_DEFS,
                    tool_choice="auto",
                    temperature=1.0,
                    max_tokens=2000,
                )
                msg = resp.choices[0].message

                if not msg.tool_calls:
                    text = msg.content or "小镜已为您处理完毕～"
                    full_messages.append({"role": "assistant", "content": text})
                    yield f"data: {_json.dumps({'type':'text','delta':text},ensure_ascii=False)}\n\n"
                    yield f"data: {_json.dumps({'type':'done','messages':full_messages[1:]},ensure_ascii=False)}\n\n"
                    return

                full_messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
                })
                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    fn_args = _json.loads(tc.function.arguments)
                    yield f"data: {_json.dumps({'type':'tool_start','name':fn_name})}\n\n"
                    handler = tool_handlers.get(fn_name)
                    try:
                        result = await handler(**fn_args) if handler else {"error": f"Unknown: {fn_name}"}
                    except Exception as exc:
                        result = {"error": str(exc)}
                    yield f"data: {_json.dumps({'type':'tool_result','name':fn_name,'result':result},ensure_ascii=False)}\n\n"
                    full_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _json.dumps(result, ensure_ascii=False),
                    })

            yield f"data: {_json.dumps({'type':'done','messages':full_messages[1:]})}\n\n"

        except Exception as exc:
            logger.exception("Resale chat error: %s", exc)
            yield f"data: {_json.dumps({'type':'error','message':str(exc)[:200]})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
