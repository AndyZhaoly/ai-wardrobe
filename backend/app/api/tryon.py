"""
Try-on API endpoints.

POST /tryon/selfie              — upload selfie, run GSAM, return session_id
GET  /tryon/{session_id}        — poll session status
GET  /tryon/item-image/{item_id} — serve demo item images
POST /tryon/chat                — stream chat with 小镜 (SSE, async generator)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from arq import create_pool
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.mirror_agent import MirrorAgent
from app.agent.tools.tryon_tools import (
    handle_add_to_wardrobe,
    handle_show_recommendations,
    handle_trigger_virtual_tryon,
    handle_try_all_lower,
)
from app.config import get_settings
from app.database import get_db
from app.models.item import ClothingItem
from app.models.tryon import TryonSession
from app.services.gsam_client import GSAMClient
from app.utils.auth import CurrentUser
from app.workers.settings import get_redis_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/tryon", tags=["Try-On"])

_agent = MirrorAgent()
_gsam = GSAMClient(check_health=False)


# ── schemas ───────────────────────────────────────────────────────────────────

class SelfieUploadResponse(BaseModel):
    session_id: str
    upper_detected: bool
    lower_detected: bool
    upper_image_url: str | None = None
    lower_image_url: str | None = None


class TryonStatusResponse(BaseModel):
    session_id: str
    status: str
    result_image_url: str | None = None
    error_message: str | None = None


class ChatRequest(BaseModel):
    session_id: str
    message: str
    history: list[dict] = []


# ── helpers ───────────────────────────────────────────────────────────────────

def _save_pil(img: Image.Image, rel_path: str) -> str:
    abs_path = Path(settings.storage_path) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(abs_path))
    return rel_path


def _result_image_url(rel_path: str | None) -> str | None:
    if not rel_path:
        return None
    return f"/api/v1/tryon/result-image/{rel_path}"


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/selfie", response_model=SelfieUploadResponse)
async def upload_selfie(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
) -> SelfieUploadResponse:
    """Upload selfie → GSAM segmentation → create TryonSession."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an image")

    session_id = uuid.uuid4()
    rel_person = f"tryon/selfies/{session_id}.jpg"
    abs_person = Path(settings.storage_path) / rel_person
    abs_person.parent.mkdir(parents=True, exist_ok=True)

    contents = await file.read()
    with open(str(abs_person), "wb") as f:
        f.write(contents)

    tryon = TryonSession(
        id=session_id,
        user_id=current_user.id,
        person_image_path=rel_person,
        status="pending",
    )
    db.add(tryon)
    await db.commit()

    upper_url = lower_url = None
    upper_detected = lower_detected = False

    if _gsam.available:
        try:
            upper_imgs, _ = _gsam.extract_upper_body(str(abs_person))
            lower_imgs, _ = _gsam.extract_lower_body(str(abs_person))

            if upper_imgs:
                rel = _save_pil(upper_imgs[0], f"tryon/segments/{session_id}_upper.png")
                upper_url = f"/api/v1/tryon/segment-image/{rel}"
                upper_detected = True

            if lower_imgs:
                rel = _save_pil(lower_imgs[0], f"tryon/segments/{session_id}_lower.png")
                lower_url = f"/api/v1/tryon/segment-image/{rel}"
                lower_detected = True

        except Exception as exc:
            logger.warning("GSAM failed for session %s: %s", session_id, exc)

    return SelfieUploadResponse(
        session_id=str(session_id),
        upper_detected=upper_detected,
        lower_detected=lower_detected,
        upper_image_url=upper_url,
        lower_image_url=lower_url,
    )


@router.get("/item-image/{item_id}")
async def get_item_image(
    item_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileResponse:
    """Serve a demo clothing item image."""
    item = await db.get(ClothingItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    abs_path = Path(settings.storage_path) / item.image_path
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    return FileResponse(str(abs_path))


@router.get("/segment-image/{path:path}")
async def get_segment_image(path: str, current_user: CurrentUser) -> FileResponse:
    """Serve a GSAM segmentation image."""
    abs_path = Path(settings.storage_path) / path
    if not abs_path.exists() or not abs_path.is_relative_to(Path(settings.storage_path)):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(abs_path))


@router.get("/result-image/{path:path}")
async def get_result_image(path: str, current_user: CurrentUser) -> FileResponse:
    """Serve a VTON result image."""
    abs_path = Path(settings.storage_path) / path
    if not abs_path.exists() or not abs_path.is_relative_to(Path(settings.storage_path)):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(abs_path))


@router.get("/{session_id}", response_model=TryonStatusResponse)
async def get_tryon_status(
    session_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TryonStatusResponse:
    """Poll try-on session status."""
    tryon = await db.get(TryonSession, session_id)
    if not tryon or tryon.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    return TryonStatusResponse(
        session_id=str(tryon.id),
        status=tryon.status,
        result_image_url=_result_image_url(tryon.result_image_path),
        error_message=tryon.error_message,
    )


@router.post("/chat")
async def chat(
    request: ChatRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """
    Chat with 小镜. SSE stream — each line: `data: <json>\\n\\n`
    Event types: text | tool_start | tool_result | done | error
    """
    session_uuid = uuid.UUID(request.session_id)
    tryon = await db.get(TryonSession, session_uuid)
    if not tryon or tryon.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    person_image_path = tryon.person_image_path

    # Tool handlers — all async, using the request's db session
    async def _show_recs() -> dict:
        return await handle_show_recommendations(db, current_user.id)

    async def _trigger_vton(garment_item_id: str) -> dict:
        result = await handle_trigger_virtual_tryon(
            db, current_user.id, person_image_path, garment_item_id
        )
        if result.get("status") == "queued":
            try:
                redis = await create_pool(get_redis_settings())
                await redis.enqueue_job("run_vton_job", result["tryon_session_id"])
                await redis.aclose()
            except Exception as exc:
                logger.warning("Failed to queue VTON job: %s", exc)
        return result

    async def _try_all() -> dict:
        result = await handle_try_all_lower(db, current_user.id, person_image_path)
        if result.get("status") == "queued":
            try:
                redis = await create_pool(get_redis_settings())
                for sid in result.get("session_ids", []):
                    await redis.enqueue_job("run_vton_job", sid)
                await redis.aclose()
            except Exception as exc:
                logger.warning("Failed to queue VTON jobs: %s", exc)
        return result

    async def _add_wardrobe() -> dict:
        return await handle_add_to_wardrobe(db, current_user.id)

    tool_handlers = {
        "show_recommendations": _show_recs,
        "trigger_virtual_tryon": _trigger_vton,
        "try_all_lower": _try_all,
        "add_to_wardrobe": _add_wardrobe,
    }

    async def generate():
        async for event in _agent.stream_chat(request.history, tool_handlers):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
