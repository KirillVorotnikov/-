"""VLM backend factory."""

from __future__ import annotations

from doc_converter.config import Settings
from doc_converter.vlm.base import VLMBackend
from doc_converter.vlm.mock_backend import MockVLMBackend


def get_vlm_backend(settings: Settings) -> VLMBackend | None:
    """Return configured VLM backend or ``None`` when VLM is disabled."""
    if settings.vlm_backend == "off":
        return None
    if settings.vlm_backend == "mock":
        return MockVLMBackend()
    if settings.vlm_backend == "openai":
        msg = "OpenAI VLM backend is planned for phase 4"
        raise NotImplementedError(msg)
    if settings.vlm_backend == "anthropic":
        msg = "Anthropic VLM backend is planned for phase 4"
        raise NotImplementedError(msg)
    if settings.vlm_backend == "local":
        msg = "Local VLM backend is planned for phase 4"
        raise NotImplementedError(msg)
    msg = f"Unknown VLM backend: {settings.vlm_backend}"
    raise ValueError(msg)
