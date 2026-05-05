"""arq job: run IDM-VTON try-on for a TryonSession."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, UTC
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.models.tryon import TryonSession
from app.services.gsam_client import GSAMClient
from app.services.vton_client import VTONClient

logger = logging.getLogger(__name__)


async def run_vton_job(ctx: dict, session_id: str) -> dict:
    """
    arq job entry point.
    ctx["db"] is injected by WorkerSettings.on_startup.
    """
    settings = get_settings()
    async with ctx["db"]() as db:
        session_uuid = uuid.UUID(session_id)
        tryon = await db.get(TryonSession, session_uuid)
        if tryon is None:
            logger.error("run_vton_job: session %s not found", session_id)
            return {"error": "session not found"}

        tryon.status = "processing"
        await db.commit()

        try:
            vton = VTONClient(check_health=False)
            if not vton.available:
                raise RuntimeError(f"VTON service not reachable at {settings.vton_url}")

            garment_path: str | None = None
            if tryon.garment_item_id:
                from app.models.item import ClothingItem
                garment = await db.get(ClothingItem, tryon.garment_item_id)
                if garment:
                    garment_path = _resolve(garment.image_path, settings.storage_path)

            if not garment_path or not os.path.exists(garment_path):
                raise RuntimeError("Garment image not found on disk")

            person_path = _resolve(tryon.person_image_path, settings.storage_path)
            if not os.path.exists(person_path):
                raise RuntimeError("Person image not found on disk")

            result_img = vton.try_on(
                person_image_path=person_path,
                clothes_image_path=garment_path,
                clothing_category=tryon.clothing_category or "lower_body",
                preserve_face=True,
            )

            # Save result
            out_dir = Path(settings.storage_path) / "tryon_results"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{session_id}.png"
            result_img.save(str(out_path))

            rel_path = str(out_path.relative_to(settings.storage_path))
            tryon.result_image_path = rel_path
            tryon.status = "done"
            tryon.completed_at = datetime.now(UTC)

        except Exception as exc:
            logger.exception("run_vton_job failed for session %s: %s", session_id, exc)
            tryon.status = "error"
            tryon.error_message = str(exc)[:500]

        await db.commit()
        return {"status": tryon.status, "session_id": session_id}


def _resolve(path: str, storage_root: str) -> str:
    """Turn a relative storage path into an absolute path."""
    if os.path.isabs(path):
        return path
    return os.path.join(storage_root, path)
