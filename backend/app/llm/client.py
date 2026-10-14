"""Anthropic client helpers."""
from __future__ import annotations

import base64
import io
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic

from ..config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, LLM_MODEL, LLM_PROVIDER

# Both providers speak the Anthropic Messages API: Claude natively, DeepSeek through
# its Anthropic-compatible endpoint. One client type, one agent loop.


@lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    if LLM_PROVIDER == "deepseek":
        return anthropic.Anthropic(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL, max_retries=3)
    # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile.
    return anthropic.Anthropic(max_retries=3)


def messages_api() -> Any:
    """`client.beta.messages` for Claude (needed for refusal fallbacks), plain `client.messages` otherwise."""
    return get_client().beta.messages if LLM_PROVIDER == "anthropic" else get_client().messages


def provider_kwargs() -> dict[str, Any]:
    """Provider-specific request extras."""
    if LLM_PROVIDER == "anthropic":
        # Server-side refusal fallback: if a safety classifier declines a request,
        # the API transparently re-runs it on Anthropic's recommended fallback model.
        return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    return {}


def effort(level: str) -> str:
    """Map the UI's low/medium/high onto what the provider supports (DeepSeek: low/high/max)."""
    if LLM_PROVIDER == "deepseek":
        return {"low": "low", "medium": "high", "high": "max"}.get(level, "high")
    return level


def model_label() -> str:
    return f"{LLM_PROVIDER}:{LLM_MODEL}"


def llm_available() -> bool:
    import os

    if LLM_PROVIDER == "deepseek":
        return bool(DEEPSEEK_API_KEY)
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
        key = "DEEPSEEK_API_KEY" if LLM_PROVIDER == "deepseek" else "ANTHROPIC_API_KEY"
        return f"{LLM_PROVIDER.title()} authentication failed — check {key} in .env and restart the backend."
    if isinstance(e, anthropic.RateLimitError):
        return f"Rate limited by the {LLM_PROVIDER.title()} API — please retry in a moment."
    if isinstance(e, anthropic.APIStatusError):
        return f"{LLM_PROVIDER.title()} API error {e.status_code}: {getattr(e, 'message', str(e))[:300]}"
    if isinstance(e, anthropic.APIConnectionError):
        return f"Could not reach the {LLM_PROVIDER.title()} API (network error)."
    return f"{type(e).__name__}: {str(e)[:300]}"
