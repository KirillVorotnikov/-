"""Image processing orchestrator."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from doc_converter.config import Settings
from doc_converter.image_pipeline.classifier import classify_image
from doc_converter.image_pipeline.diagram_extractor import extract_diagram
from doc_converter.image_pipeline.prompts import CHART_DESCRIPTION_PROMPT, IMAGE_CAPTION_PROMPT
from doc_converter.image_pipeline.table_extractor import extract_table
from doc_converter.ir import DocElement
from doc_converter.vlm.factory import get_vlm_backend

logger = logging.getLogger(__name__)


def _output_media_dir(settings: Settings) -> Path:
    return Path(settings.output_dir) / "media"


def persist_media_file(source: Path, settings: Settings) -> str:
    """Copy *source* into output media directory and return a relative path."""
    media_dir = _output_media_dir(settings)
    media_dir.mkdir(parents=True, exist_ok=True)

    destination = media_dir / source.name
    if destination.exists():
        if destination.read_bytes() == source.read_bytes():
            return f"media/{destination.name}"
        stem = source.stem
        suffix = source.suffix
        counter = 1
        while destination.exists():
            destination = media_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    shutil.copy2(source, destination)
    return f"media/{destination.name}"


def _image_element(
    relative_path: str,
    *,
    caption: str | None,
    extraction_method: str,
    confidence: float | None,
) -> DocElement:
    return DocElement(
        type="image",
        content=relative_path,
        raw_image_path=relative_path,
        caption=caption,
        extraction_method=extraction_method,
        confidence=confidence,
    )


def process_image(
    image_path: Path,
    settings: Settings,
    *,
    caption: str | None = None,
    extraction_method: str | None = None,
) -> DocElement:
    """Classify and extract structured content from an image."""
    relative_path = persist_media_file(image_path.resolve(), settings)
    stored_path = Path(settings.output_dir) / relative_path

    vlm = get_vlm_backend(settings)
    kind, classify_method, classify_confidence = classify_image(stored_path, settings, vlm)
    logger.debug("Image %s classified as %s via %s", stored_path.name, kind, classify_method)

    if kind == "table":
        table_element = extract_table(
            stored_path,
            settings,
            vlm,
            raw_image_path=relative_path,
        )
        if table_element is not None:
            return table_element

    if kind == "diagram":
        if vlm is not None:
            graph_element = extract_diagram(
                stored_path,
                settings,
                vlm,
                raw_image_path=relative_path,
            )
            if graph_element is not None:
                return graph_element
            return _image_element(
                relative_path,
                caption=caption,
                extraction_method="vlm-failed-fallback-image",
                confidence=0.3,
            )
        return _image_element(
            relative_path,
            caption=caption or "",
            extraction_method=classify_method,
            confidence=classify_confidence,
        )

    resolved_caption = caption
    method = extraction_method or classify_method
    confidence = classify_confidence

    if vlm is not None:
        if kind == "chart":
            resolved_caption = vlm.ask(stored_path, CHART_DESCRIPTION_PROMPT).strip() or caption
            method = f"vlm-{getattr(vlm, 'backend_name', 'unknown')}-chart"
        elif kind in ("photo", "formula", "text", "mixed"):
            auto_caption = vlm.ask(stored_path, IMAGE_CAPTION_PROMPT).strip()
            resolved_caption = caption or auto_caption or None
            method = f"vlm-{getattr(vlm, 'backend_name', 'unknown')}-caption"

    return _image_element(
        relative_path,
        caption=resolved_caption,
        extraction_method=method,
        confidence=confidence,
    )
