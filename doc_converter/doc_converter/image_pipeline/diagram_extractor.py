"""Extract graph structures from diagram images."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from doc_converter.config import Settings
from doc_converter.ir import DocElement
from doc_converter.image_pipeline.mermaid import GraphExtraction, graph_json_to_mermaid
from doc_converter.image_pipeline.prompts import DIAGRAM_EXTRACTION_PROMPT, DIAGRAM_JSON_RETRY_PROMPT
from doc_converter.vlm.base import VLMBackend

logger = logging.getLogger(__name__)


def _parse_graph_response(raw: str) -> GraphExtraction:
    return GraphExtraction.model_validate_json(raw)


def extract_diagram(
    image_path: Path,
    settings: Settings,
    vlm: VLMBackend | None,
    *,
    raw_image_path: str,
) -> DocElement | None:
    """Extract a Mermaid graph element from a diagram image."""
    if vlm is None:
        return None

    backend = getattr(vlm, "backend_name", vlm.__class__.__name__)
    method = f"vlm-{backend}"

    raw = vlm.ask(image_path, DIAGRAM_EXTRACTION_PROMPT, expect_json=True)
    graph: GraphExtraction | None = None
    try:
        graph = _parse_graph_response(raw)
    except ValidationError:
        logger.warning("Invalid diagram JSON from VLM, retrying once")
        retry_raw = vlm.ask(image_path, DIAGRAM_JSON_RETRY_PROMPT, expect_json=True)
        try:
            graph = _parse_graph_response(retry_raw)
        except ValidationError:
            logger.error("Diagram JSON invalid after retry for %s", image_path)
            return None

    if graph is None or not graph.nodes:
        return None

    mermaid = graph_json_to_mermaid(graph)
    return DocElement(
        type="graph",
        content=mermaid,
        caption=graph.title or None,
        raw_image_path=raw_image_path,
        extraction_method=method,
        confidence=0.74,
    )
