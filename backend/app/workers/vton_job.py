"""arq job: run IDM-VTON try-on for a TryonSession."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.models.tryon import TryonSession
from app.services.vton_client import VTONClient
from app.workers.db import get_db_session

logger = logging.getLogger(__name__)


async def run_vton_job(ctx: dict, session_id: str) -> dict:
    """arq job — fetch the TryonSession, call IDM-VTON, save result."""
    settings = get_settings()
    session_uuid = uuid.UUID(session_id)

    # Mark as processing
    db = get_db_session(ctx)
    try:
        tryon = await db.get(TryonSession, session_uuid)
        if tryon is None:
            logger.error("run_vton_job: session %s not found", session_id)
            return {"error": "session not found"}
        tryon.status = "processing"
        await db.commit()
    finally:
        await db.close()

    # Run VTON (can take up to ~60s)
    vton = VTONClient(check_health=False)
    result_status = "error"
    result_path: str | None = None
    error_msg: str | None = None

    try:
        if not vton.available:
            raise RuntimeError(f"VTON service not reachable at {settings.vton_url}")

        db2 = get_db_session(ctx)
        try:
            tryon2 = await db2.get(TryonSession, session_uuid)
            if tryon2 is None:
                raise RuntimeError("Session disappeared")

            garment_path: str | None = None
            if tryon2.garment_item_id:
                from app.models.item import ClothingItem
                garment = await db2.get(ClothingItem, tryon2.garment_item_id)
                if garment:
                    garment_path = _resolve(garment.image_path, settings.storage_path)

            if not garment_path or not os.path.exists(garment_path):
                raise RuntimeError("Garment image not found on disk")

            person_path = _resolve(tryon2.person_image_path, settings.storage_path)
            if not os.path.exists(person_path):
                raise RuntimeError("Person image not found on disk")

            category = tryon2.clothing_category or "lower_body"
        finally:
            await db2.close()

        result_img = vton.try_on(
            person_image_path=person_path,
            clothes_image_path=garment_path,
            clothing_category=category,
            preserve_face=True,
        )

        out_dir = Path(settings.storage_path) / "tryon_results"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{session_id}.png"
        result_img.save(str(out_file))
        result_path = str(out_file.relative_to(settings.storage_path))
        result_status = "done"

    except Exception as exc:
        logger.exception("run_vton_job failed for session %s: %s", session_id, exc)
        error_msg = str(exc)[:500]

    # Write final status
    db3 = get_db_session(ctx)
    try:
        tryon3 = await db3.get(TryonSession, session_uuid)
        if tryon3:
            tryon3.status = result_status
            tryon3.result_image_path = result_path
            tryon3.error_message = error_msg
            if result_status == "done":
                tryon3.completed_at = datetime.now(UTC)
            await db3.commit()
    finally:
        await db3.close()

    return {"status": result_status, "session_id": session_id}


def _resolve(path: str, storage_root: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(storage_root, path)
