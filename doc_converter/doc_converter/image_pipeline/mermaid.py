"""Mermaid diagram generation from extracted graph JSON."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    id: str
    label: str
    type: Literal[
        "process",
        "equipment",
        "material",
        "input",
        "output",
        "decision",
        "other",
    ] = "other"


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str | None = None
    direction: Literal["forward", "bidirectional"] = "forward"


class GraphExtraction(BaseModel):
    title: str = ""
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


def _escape_mermaid_label(label: str) -> str:
    """Escape characters that break Mermaid node labels."""
    escaped = label.replace('"', "'")
    escaped = re.sub(r"[\[\]{}]", "", escaped)
    return escaped


def graph_json_to_mermaid(data: GraphExtraction) -> str:
    """Convert validated graph extraction to Mermaid flowchart syntax."""
    lines = ["flowchart TD"]
    if data.title:
        lines.insert(0, f"%% {data.title}")

    for node in data.nodes:
        label = _escape_mermaid_label(node.label)
        lines.append(f'    {node.id}["{label}"]')

    for edge in data.edges:
        if edge.direction == "bidirectional":
            arrow = "<-->"
        else:
            arrow = "-->"
        if edge.label:
            edge_label = _escape_mermaid_label(edge.label)
            lines.append(f"    {edge.source} {arrow}|{edge_label}| {edge.target}")
        else:
            lines.append(f"    {edge.source} {arrow} {edge.target}")

    return "\n".join(lines)
