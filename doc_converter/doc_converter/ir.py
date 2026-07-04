"""Intermediate representation (IR) for parsed documents."""

from dataclasses import dataclass, field
from typing import Literal

ElementType = Literal[
    "heading",
    "paragraph",
    "table",
    "image",
    "graph",
    "list",
    "formula",
    "code",
]

SourceType = Literal["docx", "pdf", "xlsx", "png", "jpg", "jpeg"]


@dataclass
class DocElement:
    """Single structural unit extracted from a source document."""

    type: ElementType
    content: str = ""
    html_content: str | None = None
    level: int | None = None
    caption: str | None = None
    source_page: int | None = None
    source_sheet: str | None = None
    raw_image_path: str | None = None
    extraction_method: str | None = None
    confidence: float | None = None


@dataclass
class ParsedDocument:
    """Format-agnostic parsed document ready for rendering."""

    source_file: str
    source_type: SourceType
    elements: list[DocElement] = field(default_factory=list)
