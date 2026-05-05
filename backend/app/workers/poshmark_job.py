"""arq job: run Playwright Poshmark automation for a ResaleListing."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from app.config import get_settings
from app.models.tryon import ResaleListing
from app.workers.db import get_db_session

logger = logging.getLogger(__name__)


async def run_poshmark_job(ctx: dict, listing_id: str) -> dict:
    """
    arq job — fetch ResaleListing, run poshmark_bot, update status.
    Runs headless with auto_submit=False (fills form, takes screenshot, returns).
    """
    settings = get_settings()
    listing_uuid = uuid.UUID(listing_id)

    db = get_db_session(ctx)
    try:
        listing = await db.get(ResaleListing, listing_uuid)
        if listing is None:
            return {"error": "listing not found"}

        image_path = listing.cropped_image_path or listing.garment_image_path
        abs_image = _resolve(image_path, settings.storage_path)

        if not os.path.exists(abs_image):
            listing.status = "error"
            await db.commit()
            return {"error": f"Image not found: {abs_image}"}

        title = listing.listing_title or "Item"
        description = listing.listing_description or ""
        category_path = listing.poshmark_category_path or ["Women", "Jackets & Coats"]
        listing_price = str(listing.listing_price_usd or 0)
        original_price = str(_cny_to_usd(listing.original_price_cny or 0))

    finally:
        await db.close()

    # Run Playwright synchronously (arq jobs are async but we call sync Playwright API)
    result = _run_bot(
        image_path=abs_image,
        title=title,
        description=description,
        listing_price=listing_price,
        original_price=original_price,
        category_path=category_path,
    )

    db2 = get_db_session(ctx)
    try:
        listing2 = await db2.get(ResaleListing, listing_uuid)
        if listing2:
            if result.get("success"):
                listing2.status = "posted"
                listing2.poshmark_listing_id = result.get("data", {}).get("listing_id", "")
            else:
                listing2.status = "error"
            await db2.commit()
    finally:
        await db2.close()

    return {"status": result.get("status", "error"), "listing_id": listing_id}


def _run_bot(
    image_path: str,
    title: str,
    description: str,
    listing_price: str,
    original_price: str,
    category_path: list,
) -> dict:
    """Call poshmark_bot synchronously — Playwright is sync-only."""
    try:
        from app.tools.poshmark_bot import create_poshmark_listing
        return create_poshmark_listing(
            image_path=image_path,
            title=title[:50],
            description=description,
            original_price=original_price,
            listing_price=listing_price,
            category_path=category_path,
            headless=True,
            auto_submit=False,
        )
    except Exception as exc:
        logger.exception("poshmark_bot failed: %s", exc)
        return {"success": False, "status": "error", "message": str(exc)}


def _resolve(path: str, storage_root: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(storage_root, path)


def _cny_to_usd(amount: int | float) -> int:
    return max(1, round(amount * 0.14))
