"""
Grounded-SAM client — calls the remote GSAM segmentation service.
Ported from ai-mirror-demo/gsam_client.py with minimal changes.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

import requests
from PIL import Image

from app.config import get_settings

logger = logging.getLogger(__name__)


class GSAMClient:
    """HTTP client for the Grounded-SAM segmentation microservice."""

    def __init__(self, service_url: str | None = None, check_health: bool = True) -> None:
        self.service_url = (service_url or get_settings().gsam_url).rstrip("/")
        self.available = False
        try:
            self.health_check()
            self.available = True
        except Exception as exc:
            logger.warning("GSAMClient: service unavailable at %s — %s", self.service_url, exc)

    # ── internal ──────────────────────────────────────────────────────────────

    def _decode_images(self, b64_list: list[str]) -> list[Image.Image]:
        images = []
        for b64 in b64_list:
            img_bytes = base64.b64decode(b64)
            images.append(Image.open(io.BytesIO(img_bytes)).copy())
        return images

    def _post(self, endpoint: str, image_path: str, extra_data: dict[str, Any] | None = None) -> dict:
        url = f"{self.service_url}/{endpoint.lstrip('/')}"
        data = extra_data or {}
        with open(image_path, "rb") as f:
            response = requests.post(url, files={"image": f}, data=data, timeout=300)
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "success":
            raise RuntimeError(f"GSAM {endpoint} failed: {result.get('message', 'unknown error')}")
        return result

    def _detection_info(self, result: dict) -> dict[str, Any]:
        return {
            "bounding_boxes": result.get("bounding_boxes", []),
            "labels": result.get("labels", []),
            "confidences": result.get("confidences", []),
        }

    # ── public API ────────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        response = requests.get(f"{self.service_url}/health", timeout=10)
        response.raise_for_status()
        return response.json()

    def segment_clothing(
        self,
        image_path: str,
        prompt: str = "clothes",
        box_threshold: float = 0.25,
        text_threshold: float = 0.25,
        white_background: bool = True,
    ) -> list[Image.Image]:
        result = self._post(
            "extract_clothes",
            image_path,
            {"prompt": prompt, "box_threshold": box_threshold,
             "text_threshold": text_threshold, "white_background": white_background},
        )
        return self._decode_images(result.get("segmented_images", []))

    def extract_upper_body(
        self, image_path: str, white_background: bool = True
    ) -> tuple[list[Image.Image], dict[str, Any]]:
        result = self._post("extract_upper_body", image_path, {"white_background": white_background})
        return self._decode_images(result.get("segmented_images", [])), self._detection_info(result)

    def extract_lower_body(
        self, image_path: str, white_background: bool = True
    ) -> tuple[list[Image.Image], dict[str, Any]]:
        result = self._post("extract_lower_body", image_path, {"white_background": white_background})
        return self._decode_images(result.get("segmented_images", [])), self._detection_info(result)

    def extract_shoes(
        self, image_path: str, white_background: bool = True
    ) -> tuple[list[Image.Image], dict[str, Any]]:
        result = self._post(
            "extract_clothes", image_path,
            {"prompt": "shoes, sneakers, boots, heels, sandals", "white_background": white_background},
        )
        return self._decode_images(result.get("segmented_images", [])), self._detection_info(result)
