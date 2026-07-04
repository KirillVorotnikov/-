"""PDF → ParsedDocument converter using marker-pdf + PyMuPDF."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from doc_converter.config import Settings
from doc_converter.converters.base import BaseConverter
from doc_converter.converters.pdf.grobid_plugin import extract_grobid_metadata, is_academic_document
from doc_converter.converters.pdf.marker_runner import run_marker_pdf
from doc_converter.converters.pdf.page_split import split_marker_pages
from doc_converter.converters.pdf.pymupdf_utils import ExtractedFigure, extract_pdf_images, render_page_to_image
from doc_converter.converters.pdf.table_heuristics import is_low_quality_table
from doc_converter.image_pipeline.processor import persist_media_file, process_image
from doc_converter.image_pipeline.table_extractor import extract_table
from doc_converter.ir import DocElement, ParsedDocument
from doc_converter.utils.markdown_parser import parse_markdown
from doc_converter.vlm.factory import get_vlm_backend

logger = logging.getLogger(__name__)


class PdfConverter(BaseConverter):
    """Convert PDF documents via marker-pdf with PyMuPDF figure extraction."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def parse(self, path: Path) -> ParsedDocument:
        path = path.resolve()

        with tempfile.TemporaryDirectory(prefix="doc_convert_pdf_") as tmp:
            tmp_dir = Path(tmp)
            figures_dir = tmp_dir / "figures"
            render_dir = tmp_dir / "render"

            marker_markdown = run_marker_pdf(path)
            page_blocks = split_marker_pages(marker_markdown)
            figures = extract_pdf_images(path, figures_dir)
            figures_by_page = _group_figures_by_page(figures)

            elements: list[DocElement] = []

            if self._should_run_grobid(marker_markdown):
                grobid_url = self.settings.grobid_server or "http://localhost:8070"
                elements.extend(extract_grobid_metadata(path, grobid_url))

            for page_index, page_markdown in page_blocks:
                display_page = page_index + 1
                page_elements = parse_markdown(page_markdown, extraction_method="marker")
                page_elements = self._tag_source_page(page_elements, display_page)
                page_elements = self._fix_low_quality_tables(
                    page_elements,
                    pdf_path=path,
                    page_num=display_page,
                    render_dir=render_dir,
                )
                elements.extend(page_elements)
                elements.extend(
                    self._process_page_figures(
                        figures_by_page.get(display_page, []),
                        page_num=display_page,
                    )
                )

        return ParsedDocument(
            source_file=str(path),
            source_type="pdf",
            elements=elements,
        )

    def _should_run_grobid(self, marker_markdown: str) -> bool:
        if self.settings.academic:
            return True
        return is_academic_document(marker_markdown)

    def _tag_source_page(self, elements: list[DocElement], page_num: int) -> list[DocElement]:
        for element in elements:
            element.source_page = page_num
        return elements

    def _fix_low_quality_tables(
        self,
        elements: list[DocElement],
        *,
        pdf_path: Path,
        page_num: int,
        render_dir: Path,
    ) -> list[DocElement]:
        fixed: list[DocElement] = []
        vlm = get_vlm_backend(self.settings)

        for element in elements:
            if element.type != "table" or not is_low_quality_table(element.content):
                fixed.append(element)
                continue

            logger.info("Re-extracting low-quality table on page %s", page_num)
            page_image = render_dir / f"page{page_num}_render.png"
            try:
                render_page_to_image(pdf_path, page_num, page_image)
            except Exception:
                logger.exception("Failed to render page %s for table fallback", page_num)
                fixed.append(element)
                continue

            relative_render = persist_media_file(page_image, self.settings)
            replacement = extract_table(
                page_image,
                self.settings,
                vlm,
                raw_image_path=relative_render,
            )
            if replacement is None:
                fixed.append(element)
                continue

            replacement.source_page = page_num
            replacement.extraction_method = f"{replacement.extraction_method}+page-rerender"
            fixed.append(replacement)

        return fixed

    def _process_page_figures(
        self,
        figures: list[ExtractedFigure],
        *,
        page_num: int,
    ) -> list[DocElement]:
        processed: list[DocElement] = []
        for figure in figures:
            element = process_image(
                figure.path,
                self.settings,
                extraction_method="pymupdf",
            )
            element.source_page = page_num
            processed.append(element)
        return processed


def _group_figures_by_page(figures: list[ExtractedFigure]) -> dict[int, list[ExtractedFigure]]:
    grouped: dict[int, list[ExtractedFigure]] = {}
    for figure in figures:
        grouped.setdefault(figure.page_num, []).append(figure)
    return grouped
