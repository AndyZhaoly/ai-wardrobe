"""
Tool handlers for the try-on flow.
These are called by MirrorAgent when Gemini invokes a function.
Each handler receives a db session + current user via a closure built in the router.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.item import ClothingItem
from app.models.tryon import TryonSession

logger = logging.getLogger(__name__)


async def handle_show_recommendations(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """Return demo garments available for try-on."""
    result = await db.execute(
        select(ClothingItem)
        .where(ClothingItem.is_demo == True, ClothingItem.is_archived == False)  # noqa: E712
        .order_by(ClothingItem.created_at)
        .limit(10)
    )
    items = result.scalars().all()

    if not items:
        return {"status": "empty", "message": "暂无推荐单品，请联系管理员添加示例服装", "items": []}

    return {
        "status": "success",
        "count": len(items),
        "items": [
            {
                "index": i + 1,
                "item_id": str(item.id),
                "name": item.name or item.type,
                "category": item.type,
                "color": item.primary_color or "",
                "style": ", ".join(item.style or []),
                "material": item.material or "",
                "description": item.ai_description or "",
                "image_url": f"/api/v1/images/{item.image_path}",
            }
            for i, item in enumerate(items)
        ],
    }


async def handle_trigger_virtual_tryon(
    db: AsyncSession,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    garment_item_id: str,
) -> dict:
    """
    Queue a virtual try-on job.
    Creates a TryonSession record; the actual VTON runs in an arq worker.
    Returns the session ID so the frontend can poll for status.
    """
    try:
        garment_uuid = uuid.UUID(garment_item_id)
    except ValueError:
        return {"status": "error", "message": f"无效的 garment_item_id: {garment_item_id}"}

    garment = await db.get(ClothingItem, garment_uuid)
    if not garment:
        return {"status": "error", "message": "找不到该服装"}

    # Map item type to IDM-VTON category
    category_map = {"upper": "upper_body", "lower": "lower_body", "dress": "dresses"}
    vton_category = category_map.get(garment.type, "upper_body")

    # Update the existing tryon session (created at selfie upload time)
    tryon = await db.get(TryonSession, session_id)
    if tryon is None:
        return {"status": "error", "message": "试衣会话不存在，请重新上传照片"}

    tryon.garment_item_id = garment_uuid
    tryon.clothing_category = vton_category
    tryon.status = "pending"
    await db.commit()

    return {
        "status": "queued",
        "tryon_session_id": str(tryon.id),
        "message": "试衣任务已提交，正在处理中，稍候在左侧查看结果",
    }


async def handle_try_all_lower(
    db: AsyncSession,
    user_id: uuid.UUID,
    person_image_path: str,
) -> dict:
    """Queue try-on jobs for all lower-body demo items."""
    result = await db.execute(
        select(ClothingItem)
        .where(
            ClothingItem.is_demo == True,  # noqa: E712
            ClothingItem.type == "lower",
            ClothingItem.is_archived == False,  # noqa: E712
        )
    )
    items = result.scalars().all()

    if not items:
        return {"status": "error", "message": "暂无下装可试"}

    sessions = []
    for item in items:
        session = TryonSession(
            user_id=user_id,
            person_image_path=person_image_path,
            garment_item_id=item.id,
            clothing_category="lower_body",
            status="pending",
        )
        db.add(session)
        sessions.append(session)

    await db.commit()

    return {
        "status": "queued",
        "session_ids": [str(s.id) for s in sessions],
        "count": len(sessions),
        "message": f"已提交 {len(sessions)} 件下装试穿任务，正在处理中",
    }


async def handle_add_to_wardrobe(
    db: AsyncSession,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> dict:
    """Save the try-on result image as a new clothing item in the user's wardrobe."""
    tryon = await db.get(TryonSession, session_id)
    if not tryon or tryon.status != "done" or not tryon.result_image_path:
        return {"status": "error", "message": "试穿还未完成或结果不可用"}

    garment = await db.get(ClothingItem, tryon.garment_item_id) if tryon.garment_item_id else None

    new_item = ClothingItem(
        user_id=user_id,
        image_path=tryon.result_image_path,
        type=garment.type if garment else "lower",
        name=f"试穿结果：{garment.name}" if garment else "试穿结果",
        source="tryon",
        is_demo=False,
        style=garment.style if garment else [],
        colors=garment.colors if garment else [],
        primary_color=garment.primary_color if garment else None,
        material=garment.material if garment else None,
        tags={},
        season=[],
        status="ready",
        ai_processed=True,
    )
    db.add(new_item)
    await db.commit()
    await db.refresh(new_item)

    return {
        "status": "success",
        "item_id": str(new_item.id),
        "message": f"已将「{new_item.name}」纳入您的数字衣柜",
    }
