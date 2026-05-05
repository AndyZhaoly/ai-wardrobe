"""
Gemini clothing analyzer — ported from ai-mirror-demo/tools/gemini_analyzer.py.
Simplified: removed Google Search grounding (not needed for listing generation).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_CATEGORY_TREE_PATH = Path(__file__).parent.parent / "data" / "poshmark_category_tree.json"

_SYSTEM_PROMPT = """You are a professional fashion expert. Analyze the clothing item in the image.
Return ONLY a JSON object with these fields:
{
  "brand": "brand name or Unknown",
  "model_name": "product name",
  "category": "clothing category",
  "poshmark_department": "Women or Men or Kids",
  "poshmark_category": "Jackets & Coats or Tops or Pants etc",
  "material": "fabric description",
  "color": "main color",
  "condition": "Excellent / Good / Fair",
  "original_price_cny": 0,
  "resale_min_cny": 0,
  "resale_max_cny": 0,
  "description": "detailed English description for resale listing",
  "confidence": "high / medium / low"
}"""


def _load_category_tree() -> str:
    try:
        with open(_CATEGORY_TREE_PATH, encoding="utf-8") as f:
            tree = json.load(f)
        lines = []
        for dept in tree:
            cats = [c["display"] for c in dept.get("categories", [])]
            lines.append(f"{dept['display']}: {', '.join(cats[:8])}")
        return "\n".join(lines)
    except Exception:
        return "Women, Men, Kids"


def analyze_garment_image(image_path: str, api_key: str) -> dict[str, Any]:
    """
    Analyze a garment image with Gemini VLM.
    Returns structured dict; keys match poshmark_demo ITEM_INFO structure.
    """
    if not api_key:
        return {"error": "GEMINI_API_KEY not set", "success": False}
    if not os.path.exists(image_path):
        return {"error": f"Image not found: {image_path}", "success": False}

    try:
        client = genai.Client(api_key=api_key)
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    types.Part.from_text(text="Analyze this garment and return the JSON."),
                ],
            )
        ]
        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            temperature=0.2,
            max_output_tokens=2048,
        )

        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model="gemini-2.0-flash", contents=contents, config=config
                )
                result = _parse_json(resp.text)
                if result:
                    result["success"] = True
                    return result
            except Exception as exc:
                logger.warning("Gemini attempt %d failed: %s", attempt + 1, exc)
                if attempt < 2:
                    time.sleep(2 ** attempt)

        return {"error": "Gemini analysis failed after retries", "success": False}

    except Exception as exc:
        logger.exception("analyze_garment_image error: %s", exc)
        return {"error": str(exc), "success": False}


def _parse_json(text: str) -> dict | None:
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown code block
    m = re.search(r"```(?:json)?\s*(\{.*?})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try finding bare JSON object
    m = re.search(r"(\{[\s\S]*})", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None
