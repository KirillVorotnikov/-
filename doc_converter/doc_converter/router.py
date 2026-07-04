"""File type detection and converter dispatch."""

from __future__ import annotations

import logging
from pathlib import Path

from doc_converter.config import Settings
from doc_converter.converters.base import BaseConverter
from doc_converter.converters.docx_converter import DocxConverter
from doc_converter.converters.image_converter import ImageConverter
from doc_converter.converters.pdf_converter import PdfConverter
from doc_converter.converters.xlsx_converter import XlsxConverter
from doc_converter.ir import ParsedDocument, SourceType

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: dict[str, SourceType] = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpeg",
}


def detect_source_type(path: Path) -> SourceType | None:
    """Return IR source type for *path* or None if unsupported."""
    return SUPPORTED_EXTENSIONS.get(path.suffix.lower())


def get_converter(source_type: SourceType, settings: Settings) -> BaseConverter:
    """Return converter instance for *source_type*."""
    if source_type == "xlsx":
        return XlsxConverter()
    if source_type == "docx":
        return DocxConverter(settings)
    if source_type == "pdf":
        return PdfConverter(settings)
    if source_type in ("png", "jpg", "jpeg"):
        return ImageConverter(settings)
    msg = f"No converter for source type: {source_type}"
    raise ValueError(msg)


def parse_file(path: Path, settings: Settings) -> ParsedDocument:
    """Parse a single supported file."""
    source_type = detect_source_type(path)
    if source_type is None:
        msg = f"Unsupported file extension: {path.suffix}"
        raise ValueError(msg)
    converter = get_converter(source_type, settings)
    return converter.parse(path)


def iter_input_files(input_path: Path, recursive: bool = False) -> list[Path]:
    """Collect supported files from file or directory input."""
    if input_path.is_file():
        return [input_path]

    pattern = "**/*" if recursive else "*"
    files: list[Path] = []
    for candidate in input_path.glob(pattern):
        if candidate.is_file() and detect_source_type(candidate) is not None:
            files.append(candidate)
    return sorted(files)
