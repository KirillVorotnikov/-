"""DOCX → ParsedDocument converter using pandoc + markdown IR parsing."""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from doc_converter.config import Settings
from doc_converter.converters.base import BaseConverter
from doc_converter.image_pipeline.processor import process_image
from doc_converter.ir import DocElement, ParsedDocument
from doc_converter.utils.markdown_parser import parse_markdown

logger = logging.getLogger(__name__)

_INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class DocxConverter(BaseConverter):
    """Convert Word documents via pandoc markdown extraction."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def parse(self, path: Path) -> ParsedDocument:
        path = path.resolve()
        try:
            import pypandoc
        except ImportError as exc:
            msg = "DOCX support requires pypandoc: pip install 'doc-converter[docx]'"
            raise ImportError(msg) from exc

        with tempfile.TemporaryDirectory(prefix="doc_convert_") as tmp:
            media_dir = Path(tmp) / "media"
            media_dir.mkdir(parents=True, exist_ok=True)
            markdown_text = pypandoc.convert_file(
                str(path),
                "markdown",
                format="docx",
                extra_args=[f"--extract-media={media_dir}"],
            )
            elements = parse_markdown(markdown_text, extraction_method="pandoc")
            elements = self._process_images(elements, media_dir, path.parent)

        return ParsedDocument(
            source_file=str(path),
            source_type="docx",
            elements=elements,
        )

    def _process_images(
        self,
        elements: list[DocElement],
        media_dir: Path,
        source_dir: Path,
    ) -> list[DocElement]:
        processed: list[DocElement] = []
        for element in elements:
            if element.type == "image":
                resolved = self._resolve_image_path(element.content, media_dir, source_dir)
                if resolved is None:
                    logger.warning("Could not resolve extracted image: %s", element.content)
                    processed.append(element)
                    continue
                processed.append(
                    process_image(
                        resolved,
                        self.settings,
                        caption=element.caption,
                        extraction_method="pandoc",
                    )
                )
                continue

            if element.type == "paragraph" and _INLINE_IMAGE_RE.search(element.content):
                processed.extend(self._split_paragraph_images(element, media_dir, source_dir))
                continue

            processed.append(element)
        return processed

    def _split_paragraph_images(
        self,
        element: DocElement,
        media_dir: Path,
        source_dir: Path,
    ) -> list[DocElement]:
        parts: list[DocElement] = []
        cursor = 0
        for match in _INLINE_IMAGE_RE.finditer(element.content):
            before = element.content[cursor : match.start()].strip()
            if before:
                parts.append(
                    DocElement(
                        type="paragraph",
                        content=before,
                        extraction_method=element.extraction_method,
                    )
                )
            image_ref = match.group(2).strip()
            caption = match.group(1).strip() or None
            resolved = self._resolve_image_path(image_ref, media_dir, source_dir)
            if resolved is None:
                parts.append(
                    DocElement(
                        type="image",
                        content=image_ref,
                        caption=caption,
                        extraction_method=element.extraction_method,
                    )
                )
            else:
                parts.append(
                    process_image(
                        resolved,
                        self.settings,
                        caption=caption,
                        extraction_method="pandoc",
                    )
                )
            cursor = match.end()

        tail = element.content[cursor:].strip()
        if tail:
            parts.append(
                DocElement(
                    type="paragraph",
                    content=tail,
                    extraction_method=element.extraction_method,
                )
            )
        return parts

    def _resolve_image_path(
        self,
        reference: str,
        media_dir: Path,
        source_dir: Path,
    ) -> Path | None:
        ref = reference.strip().strip('"').strip("'")
        ref_path = Path(ref)

        candidates: list[Path] = []
        if ref_path.is_absolute():
            candidates.append(ref_path)
        candidates.extend(
            [
                media_dir / ref_path,
                media_dir / ref_path.name,
                source_dir / ref_path,
                source_dir / ref_path.name,
            ]
        )

        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()

        for extracted in media_dir.rglob("*"):
            if extracted.is_file() and extracted.name == ref_path.name:
                return extracted.resolve()

        return None
