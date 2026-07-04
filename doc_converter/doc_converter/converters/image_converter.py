"""Standalone PNG/JPG image converter."""

from __future__ import annotations

from pathlib import Path

from doc_converter.config import Settings
from doc_converter.converters.base import BaseConverter
from doc_converter.image_pipeline.processor import process_image
from doc_converter.ir import ParsedDocument, SourceType


class ImageConverter(BaseConverter):
    """Convert standalone image files through the image pipeline."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def parse(self, path: Path) -> ParsedDocument:
        path = path.resolve()
        suffix = path.suffix.lower().lstrip(".")
        source_type: SourceType
        if suffix == "png":
            source_type = "png"
        elif suffix in {"jpg", "jpeg"}:
            source_type = "jpg"
        else:
            msg = f"Unsupported image extension: {path.suffix}"
            raise ValueError(msg)

        element = process_image(path, self.settings, extraction_method="image-input")
        return ParsedDocument(
            source_file=str(path),
            source_type=source_type,
            elements=[element],
        )
