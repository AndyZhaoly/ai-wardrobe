"""
IDM-VTON client — calls the remote virtual try-on service.
Ported from ai-mirror-demo/idm_vton_client.py with minimal changes.
"""

from __future__ import annotations

import base64
import io
import logging

import requests
from PIL import Image

from app.config import get_settings

logger = logging.getLogger(__name__)

ClothingCategory = str  # "upper_body" | "lower_body" | "dresses"


class VTONClient:
    """HTTP client for the IDM-VTON virtual try-on microservice."""

    def __init__(self, service_url: str | None = None, check_health: bool = True) -> None:
        self.service_url = (service_url or get_settings().vton_url).rstrip("/")
        self.available = False
        try:
            self.health_check()
            self.available = True
        except Exception as exc:
            logger.warning("VTONClient: service unavailable at %s — %s", self.service_url, exc)

    # ── internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _pil_to_b64(img: Image.Image) -> str:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    @staticmethod
    def _b64_to_pil(b64: str) -> Image.Image:
        return Image.open(io.BytesIO(base64.b64decode(b64))).copy()

    # ── public API ────────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        response = requests.get(f"{self.service_url}/health", timeout=10)
        response.raise_for_status()
        return response.json()

    def try_on(
        self,
        person_image_path: str,
        clothes_image_path: str,
        prompt: str = "a photo of a person wearing clothes",
        num_inference_steps: int = 30,
        guidance_scale: float = 2.0,
        seed: int = 42,
        preserve_face: bool = True,
        clothing_category: ClothingCategory = "upper_body",
    ) -> Image.Image:
        """Run virtual try-on from file paths; returns PIL Image result."""
        if not self.available:
            raise RuntimeError("VTON service not available.")

        with open(person_image_path, "rb") as fp, open(clothes_image_path, "rb") as fc:
            response = requests.post(
                f"{self.service_url}/tryon",
                files={"person_image": fp, "clothes_image": fc},
                data={
                    "prompt": prompt,
                    "num_inference_steps": num_inference_steps,
                    "guidance_scale": guidance_scale,
                    "seed": seed,
                    "preserve_face": preserve_face,
                    "clothing_category": clothing_category,
                },
                timeout=300,
            )
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "success":
            raise RuntimeError(f"VTON failed: {result.get('message', 'unknown error')}")
        return self._b64_to_pil(result["result_image"])

    def try_on_images(
        self,
        person_image: Image.Image,
        clothes_image: Image.Image,
        prompt: str = "a photo of a person wearing clothes",
        num_inference_steps: int = 30,
        guidance_scale: float = 2.0,
        seed: int = 42,
        preserve_face: bool = True,
    ) -> Image.Image:
        """Run virtual try-on from PIL Images directly."""
        if not self.available:
            raise RuntimeError("VTON service not available.")

        response = requests.post(
            f"{self.service_url}/tryon_base64",
            data={
                "person_image_base64": self._pil_to_b64(person_image),
                "clothes_image_base64": self._pil_to_b64(clothes_image),
                "prompt": prompt,
                "num_inference_steps": num_inference_steps,
                "guidance_scale": guidance_scale,
                "seed": seed,
                "preserve_face": preserve_face,
            },
            timeout=300,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "success":
            raise RuntimeError(f"VTON failed: {result.get('message', 'unknown error')}")
        return self._b64_to_pil(result["result_image"])
