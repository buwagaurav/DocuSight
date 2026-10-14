"""Anthropic client helpers."""
from __future__ import annotations

import base64
import io
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic

# Server-side refusal fallback: if a safety classifier declines a request,
# the API transparently re-runs it on Anthropic's recommended fallback model.
FALLBACK_KW: dict[str, Any] = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


@lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile.
    return anthropic.Anthropic(max_retries=3)


def llm_available() -> bool:
    import os

    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()  # `ant auth login` profile


def image_block(path: Path, max_side: int = 1568) -> dict[str, Any]:
    """PNG page -> base64 image block, downscaled to Claude's useful resolution."""
    from PIL import Image

    img = Image.open(path)
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=88)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(buf.getvalue()).decode()}}


def response_text(resp: Any) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def describe_error(e: Exception) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return "Anthropic authentication failed — set ANTHROPIC_API_KEY in .env and restart the backend."
    if isinstance(e, anthropic.RateLimitError):
        return "Rate limited by the Anthropic API — please retry in a moment."
    if isinstance(e, anthropic.APIStatusError):
        return f"Anthropic API error {e.status_code}: {getattr(e, 'message', str(e))[:300]}"
    if isinstance(e, anthropic.APIConnectionError):
        return "Could not reach the Anthropic API (network error)."
    return f"{type(e).__name__}: {str(e)[:300]}"
