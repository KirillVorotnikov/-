"""Image content classification."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from doc_converter.config import Settings
from doc_converter.image_pipeline.prompts import CLASSIFY_PROMPT
from doc_converter.vlm.base import VLMBackend

logger = logging.getLogger(__name__)

ImageKind = Literal["table", "diagram", "chart", "photo", "formula", "text", "mixed"]

_VALID_KINDS = frozenset({"table", "diagram", "chart", "photo", "formula", "text", "mixed"})


def _heuristic_classify(image_path: Path) -> tuple[ImageKind, float]:
    """OpenCV-based fallback when VLM is unavailable."""
    stem = image_path.stem.lower()
    if "table" in stem or "таблиц" in stem:
        return "table", 0.55
    if any(token in stem for token in ("diagram", "scheme", "схем", "graph")):
        return "diagram", 0.55

    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.debug("OpenCV unavailable, defaulting image kind to photo")
        return "photo", 0.4

    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return "photo", 0.3

    edges = cv2.Canny(image, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=40, maxLineGap=10)
    if lines is not None and len(lines) > 25:
        return "table", 0.5
    return "photo", 0.4


def classify_image(
    image_path: Path,
    settings: Settings,
    vlm: VLMBackend | None,
) -> tuple[ImageKind, str, float]:
    """Classify image content and return ``(kind, method, confidence)``."""
    if vlm is not None:
        raw = vlm.ask(image_path, CLASSIFY_PROMPT).strip().lower()
        token = raw.split()[0] if raw else "photo"
        if token in _VALID_KINDS:
            method = getattr(vlm, "backend_name", vlm.__class__.__name__)
            return token, f"vlm-{method}", 0.85
        logger.warning("Unexpected VLM classification '%s', falling back to heuristics", raw)

    kind, confidence = _heuristic_classify(image_path)
    return kind, "opencv-heuristic", confidence
