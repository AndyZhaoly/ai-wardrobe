"""
Tool handlers for the Poshmark resale flow.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.tryon import ResaleListing

logger = logging.getLogger(__name__)

CNY_TO_USD = 0.14


def _cny_to_usd(amount: int | float) -> int:
    return max(1, round(amount * CNY_TO_USD))


async def handle_analyze_garment(
    db: AsyncSession,
    listing_id: uuid.UUID,
    image_path: str,
) -> dict:
    """Run Gemini VLM analysis on the uploaded garment image."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return {"error": "GEMINI_API_KEY not configured", "success": False}

    from app.services.gemini_analyzer import analyze_garment_image
    result = analyze_garment_image(image_path, settings.gemini_api_key)

    if not result.get("success"):
        return result

    # Persist analysis result into the listing row
    listing = await db.get(ResaleListing, listing_id)
    if listing:
        listing.original_price_cny = result.get("original_price_cny") or 0
        listing.listing_price_usd = _cny_to_usd(result.get("resale_max_cny") or result.get("resale_max_cny") or 0)
        listing.poshmark_category_path = [
            result.get("poshmark_department", "Women"),
            result.get("poshmark_category", "Jackets & Coats"),
        ]
        await db.commit()

    return {
        "success": True,
        "brand": result.get("brand", "Unknown"),
        "model_name": result.get("model_name", ""),
        "category": result.get("category", ""),
        "material": result.get("material", ""),
        "color": result.get("color", ""),
        "condition": result.get("condition", "Good"),
        "original_price_cny": result.get("original_price_cny", 0),
        "original_price_usd": _cny_to_usd(result.get("original_price_cny") or 0),
        "resale_min_usd": _cny_to_usd(result.get("resale_min_cny") or 0),
        "resale_max_usd": _cny_to_usd(result.get("resale_max_cny") or 0),
        "recommended_listing_usd": _cny_to_usd(result.get("resale_max_cny") or 0),
        "description": result.get("description", ""),
        "poshmark_department": result.get("poshmark_department", "Women"),
        "poshmark_category": result.get("poshmark_category", "Jackets & Coats"),
    }


async def handle_generate_poshmark_listing(
    db: AsyncSession,
    listing_id: uuid.UUID,
    item_info: dict,
) -> dict:
    """Use Gemini to write the English Poshmark listing text."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return {"error": "GEMINI_API_KEY not configured"}

    client = OpenAI(
        api_key=settings.gemini_api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    )

    prompt = f"""You are a professional Poshmark seller. Write a compelling English listing.

Item:
- Brand: {item_info.get('brand', 'Unknown')}
- Name: {item_info.get('model_name', '')}
- Category: {item_info.get('poshmark_department', 'Women')} > {item_info.get('poshmark_category', '')}
- Material: {item_info.get('material', '')}
- Color: {item_info.get('color', '')}
- Condition: {item_info.get('condition', 'Good')}
- Listing price: ${item_info.get('recommended_listing_usd', 0)}
- Original retail: ${item_info.get('original_price_usd', 0)}

Write: catchy title (≤50 chars), compelling description, size info (OS / One Size), care instructions, brief seller note.
Use emojis sparingly. Keep it natural and appealing."""

    try:
        resp = client.chat.completions.create(
            model="gemini-2.0-flash",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=800,
        )
        listing_text = resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("Listing generation failed: %s", exc)
        brand = item_info.get("brand", "")
        listing_text = (
            f"✨ {brand} {item_info.get('model_name', 'Item')} — Like New\n\n"
            f"Beautiful {item_info.get('color', '')} piece in {item_info.get('condition', 'excellent')} condition. "
            f"Material: {item_info.get('material', '')}. Size OS. "
            f"Original retail ${item_info.get('original_price_usd', 0)}."
        )

    # Save into listing row
    lines = listing_text.splitlines()
    title = lines[0].strip()[:50] if lines else "Listing"
    description = "\n".join(lines[1:]).strip() if len(lines) > 1 else listing_text

    listing = await db.get(ResaleListing, listing_id)
    if listing:
        listing.listing_title = title
        listing.listing_description = description
        await db.commit()

    return {
        "status": "success",
        "listing_text": listing_text,
        "title": title,
        "description": description,
    }


async def handle_post_to_poshmark(
    db: AsyncSession,
    listing_id: uuid.UUID,
) -> dict:
    """Queue an arq job to run the Playwright Poshmark automation."""
    listing = await db.get(ResaleListing, listing_id)
    if not listing:
        return {"error": "Listing not found"}
    if not listing.listing_title or not listing.listing_description:
        return {"error": "Please generate the listing text first"}

    listing.status = "pending_post"
    await db.commit()

    return {
        "status": "queued",
        "listing_id": str(listing_id),
        "message": "Poshmark 发布任务已提交，正在处理中",
    }
