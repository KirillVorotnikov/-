"""Sidecar metadata serialization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from doc_converter.ir import ParsedDocument


def build_metadata(
    document: ParsedDocument,
    *,
    status: str = "success",
    error: str | None = None,
) -> dict[str, Any]:
    """Build sidecar metadata dict for a parsed document."""
    elements_meta: list[dict[str, Any]] = []
    for index, element in enumerate(document.elements):
        entry: dict[str, Any] = {
            "index": index,
            "type": element.type,
        }
        if element.source_page is not None:
            entry["source_page"] = element.source_page
        if element.source_sheet is not None:
            entry["source_sheet"] = element.source_sheet
        if element.extraction_method is not None:
            entry["extraction_method"] = element.extraction_method
        if element.confidence is not None:
            entry["confidence"] = element.confidence
        if element.raw_image_path is not None:
            entry["raw_image_path"] = element.raw_image_path
        if element.caption is not None:
            entry["caption"] = element.caption
        elements_meta.append(entry)

    payload: dict[str, Any] = {
        "source_file": Path(document.source_file).name,
        "source_type": document.source_type,
        "processed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": status,
        "elements": elements_meta,
    }
    if error:
        payload["error"] = error
    return payload


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    """Write metadata JSON to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
