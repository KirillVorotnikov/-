"""PyMuPDF image extraction and page rendering."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ExtractedFigure:
    """Embedded PDF figure extracted to disk."""

    page_num: int
    path: Path
    index: int


def _require_fitz():
    try:
        import fitz
    except ImportError as exc:
        msg = "PDF image extraction requires pymupdf: pip install 'doc-converter[pdf]'"
        raise ImportError(msg) from exc
    return fitz


def extract_pdf_images(pdf_path: Path, output_dir: Path) -> list[ExtractedFigure]:
    """Extract embedded raster images from all PDF pages."""
    fitz = _require_fitz()
    output_dir.mkdir(parents=True, exist_ok=True)

    figures: list[ExtractedFigure] = []
    with fitz.open(pdf_path) as document:
        for page_index in range(document.page_count):
            page = document[page_index]
            seen_xrefs: set[int] = set()
            image_entries = page.get_images(full=True)
            for img_index, image_info in enumerate(image_entries):
                xref = int(image_info[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    extracted = document.extract_image(xref)
                except Exception:
                    logger.exception("Failed to extract image xref=%s on page %s", xref, page_index + 1)
                    continue

                extension = extracted.get("ext", "png")
                image_bytes = extracted.get("image")
                if not image_bytes:
                    continue

                out_name = f"page{page_index + 1}_img{img_index + 1}.{extension}"
                out_path = output_dir / out_name
                out_path.write_bytes(image_bytes)
                figures.append(
                    ExtractedFigure(
                        page_num=page_index + 1,
                        path=out_path,
                        index=img_index,
                    )
                )

    return figures


def render_page_to_image(pdf_path: Path, page_num: int, output_path: Path, *, zoom: float = 2.0) -> Path:
    """Render a PDF page to PNG for table re-extraction fallback."""
    fitz = _require_fitz()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(pdf_path) as document:
        if page_num < 1 or page_num > document.page_count:
            msg = f"Page {page_num} out of range for {pdf_path.name}"
            raise ValueError(msg)
        page = document[page_num - 1]
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        pixmap.save(output_path)

    return output_path
