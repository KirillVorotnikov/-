"""Mock VLM backend for tests and offline development."""

from __future__ import annotations

import json
from pathlib import Path

from doc_converter.image_pipeline.prompts import (
    CHART_DESCRIPTION_PROMPT,
    CLASSIFY_PROMPT,
    DIAGRAM_EXTRACTION_PROMPT,
    IMAGE_CAPTION_PROMPT,
    TABLE_EXTRACTION_PROMPT,
)
from doc_converter.vlm.base import VLMBackend

_VALID_KINDS = frozenset({"table", "diagram", "chart", "photo", "formula", "text", "mixed"})

_DEFAULT_DIAGRAM = {
    "title": "Mock diagram",
    "nodes": [
        {"id": "n1", "label": "Feed", "type": "input"},
        {"id": "n2", "label": "Process", "type": "process"},
    ],
    "edges": [{"source": "n1", "target": "n2", "label": "flow", "direction": "forward"}],
}

_DEFAULT_TABLE = "| Parameter | Value |\n| --- | --- |\n| Cu | 1.2 |"


class MockVLMBackend(VLMBackend):
    """Deterministic VLM stub driven by filename hints and prompt type."""

    backend_name = "mock"

    def __init__(
        self,
        *,
        classify_as: str | None = None,
        table_markdown: str = _DEFAULT_TABLE,
        diagram_json: dict[str, object] | None = None,
        fail_json_once: bool = False,
    ) -> None:
        self.classify_as = classify_as
        self.table_markdown = table_markdown
        self.diagram_json = diagram_json or dict(_DEFAULT_DIAGRAM)
        self.fail_json_once = fail_json_once
        self._json_failures = 0

    def ask(self, image_path: Path, prompt: str, expect_json: bool = False) -> str:
        stem = image_path.stem.lower()

        if prompt.strip() == CLASSIFY_PROMPT.strip() or "Определи тип содержимого" in prompt:
            if self.classify_as:
                return self.classify_as
            return self._classify_from_filename(stem)

        if "Markdown-таблицы" in prompt:
            return self.table_markdown

        if "JSON" in prompt and expect_json:
            if self.fail_json_once and self._json_failures == 0:
                self._json_failures += 1
                return "not valid json {"
            return json.dumps(self.diagram_json, ensure_ascii=False)

        if CHART_DESCRIPTION_PROMPT.strip() in prompt or "график" in prompt.lower():
            return "График показывает рост показателя Cu по оси Y."

        if IMAGE_CAPTION_PROMPT.strip() in prompt or "одной короткой фразой" in prompt:
            return f"Изображение {image_path.name}"

        if expect_json:
            return json.dumps(self.diagram_json, ensure_ascii=False)
        return "mock response"

    def _classify_from_filename(self, stem: str) -> str:
        if "table" in stem or "таблиц" in stem:
            return "table"
        if any(token in stem for token in ("diagram", "scheme", "схем", "graph")):
            return "diagram"
        if "chart" in stem or "график" in stem:
            return "chart"
        if "formula" in stem or "формул" in stem:
            return "formula"
        if "text" in stem or "scan" in stem:
            return "text"
        return "photo"
