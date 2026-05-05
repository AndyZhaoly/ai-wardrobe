"""
Try-on API endpoints.
POST /tryon/selfie         — upload selfie, run GSAM, create TryonSession
GET  /tryon/{session_id}   — poll session status
POST /tryon/chat           — chat with 小镜 (SSE streaming)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, UTC
from pathlib import Path
from typing import Annotated

from arq import create_pool
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
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

def _storage_path(*parts: str) -> Path:
    p = Path(settings.storage_path).joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _image_url(rel_path: str | None) -> str | None:
    if not rel_path:
        return None
    return f"/api/v1/images/{rel_path}"


def _save_pil(img: Image.Image, rel_path: str) -> str:
    abs_path = Path(settings.storage_path) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(abs_path))
    return rel_path


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/selfie", response_model=SelfieUploadResponse)
async def upload_selfie(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
) -> SelfieUploadResponse:
    """Upload selfie, run GSAM segmentation, return detection results + session_id."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an image")

    session_id = uuid.uuid4()
    rel_person = f"tryon/selfies/{session_id}.jpg"
    abs_person = Path(settings.storage_path) / rel_person
    abs_person.parent.mkdir(parents=True, exist_ok=True)

    contents = await file.read()
    with open(str(abs_person), "wb") as f:
        f.write(contents)

    # Create session record
    tryon = TryonSession(
        id=session_id,
        user_id=current_user.id,
        person_image_path=rel_person,
        status="pending",
    )
    db.add(tryon)
    await db.commit()

    # GSAM segmentation (graceful degradation if service down)
    upper_url = lower_url = None
    upper_detected = lower_detected = False

    if _gsam.available:
        try:
            upper_imgs, upper_det = _gsam.extract_upper_body(str(abs_person))
            lower_imgs, lower_det = _gsam.extract_lower_body(str(abs_person))

            if upper_imgs:
                rel = f"tryon/segments/{session_id}_upper.png"
                _save_pil(upper_imgs[0], rel)
                upper_url = _image_url(rel)
                upper_detected = True

            if lower_imgs:
                rel = f"tryon/segments/{session_id}_lower.png"
                _save_pil(lower_imgs[0], rel)
                lower_url = _image_url(rel)
                lower_detected = True

        except Exception as exc:
            logger.warning("GSAM segmentation failed for session %s: %s", session_id, exc)

    return SelfieUploadResponse(
        session_id=str(session_id),
        upper_detected=upper_detected,
        lower_detected=lower_detected,
        upper_image_url=upper_url,
        lower_image_url=lower_url,
    )


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
        result_image_url=_image_url(tryon.result_image_path),
        error_message=tryon.error_message,
    )


@router.post("/chat")
async def chat(
    request: ChatRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """
    Chat with 小镜. Returns an SSE stream.
    Each event is a JSON object: {"type": "text"|"tool_start"|"tool_result"|"done"|"error", ...}
    """
    session_uuid = uuid.UUID(request.session_id)
    tryon = await db.get(TryonSession, session_uuid)
    if not tryon or tryon.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Build tool handlers (closures capturing db + context)
    async def _show_recs() -> dict:
        return await handle_show_recommendations(db, current_user.id)

    async def _trigger_vton(garment_item_id: str) -> dict:
        result = await handle_trigger_virtual_tryon(
            db, current_user.id, session_uuid, garment_item_id
        )
        # Queue the arq job
        if result.get("status") == "queued":
            try:
                redis = await create_pool(get_redis_settings())
                await redis.enqueue_job("run_vton_job", result["tryon_session_id"])
                await redis.close()
            except Exception as exc:
                logger.warning("Failed to queue VTON job: %s", exc)
        return result

    async def _try_all() -> dict:
        return await handle_try_all_lower(db, current_user.id, tryon.person_image_path)

    async def _add_wardrobe() -> dict:
        return await handle_add_to_wardrobe(db, current_user.id, session_uuid)

    # Wrap async handlers for sync MirrorAgent
    # (MirrorAgent.stream_chat is sync; we run it in a thread below)
    tool_handlers_sync = {
        "show_recommendations": lambda: _run_sync(_show_recs()),
        "trigger_virtual_tryon": lambda garment_item_id: _run_sync(_trigger_vton(garment_item_id)),
        "try_all_lower": lambda: _run_sync(_try_all()),
        "add_to_wardrobe": lambda: _run_sync(_add_wardrobe()),
    }

    def generate():
        for event in _agent.stream_chat(request.history, tool_handlers_sync):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _run_sync(coro):
    """Run an async coroutine synchronously (for use inside sync generator)."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=60)
        return loop.run_until_complete(coro)
    except Exception as exc:
        return {"error": str(exc)}
