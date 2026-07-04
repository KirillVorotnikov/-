"""Render ParsedDocument to HTML via Jinja2 template."""

from __future__ import annotations

import html
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from doc_converter.ir import DocElement, ParsedDocument

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _render_heading_html(element: DocElement) -> str:
    level = element.level or 1
    level = max(1, min(level, 6))
    return f"<h{level}>{html.escape(element.content)}</h{level}>"


def _render_paragraph_html(element: DocElement) -> str:
    return f"<p>{html.escape(element.content)}</p>"


def _render_table_html(element: DocElement) -> str:
    if element.html_content:
        return element.html_content
    escaped = html.escape(element.content).replace("\n", "<br>")
    return f"<pre>{escaped}</pre>"


def _render_graph_html(element: DocElement) -> str:
    parts: list[str] = []
    if element.caption:
        parts.append(f"<p><strong>{html.escape(element.caption)}</strong></p>")
    # Mermaid blocks must stay unescaped so the runtime can parse diagram syntax
    parts.append(f'<pre class="mermaid">{element.content.strip()}</pre>')
    return "\n".join(parts)


def _render_image_html(element: DocElement) -> str:
    src = element.raw_image_path or element.content
    caption = element.caption or ""
    return (
        f'<figure><img loading="lazy" src="{html.escape(src)}" alt="{html.escape(caption)}">'
        f"<figcaption>{html.escape(caption)}</figcaption></figure>"
    )


def _render_list_html(element: DocElement) -> str:
    return element.content


def _render_formula_html(element: DocElement) -> str:
    return f"<p><em>{html.escape(element.content)}</em></p>"


def _render_code_html(element: DocElement) -> str:
    return f"<pre><code>{html.escape(element.content)}</code></pre>"


_HTML_RENDERERS = {
    "heading": _render_heading_html,
    "paragraph": _render_paragraph_html,
    "table": _render_table_html,
    "graph": _render_graph_html,
    "image": _render_image_html,
    "list": _render_list_html,
    "formula": _render_formula_html,
    "code": _render_code_html,
}


def render_element_html(element: DocElement) -> str:
    """Render a single IR element to HTML fragment."""
    renderer = _HTML_RENDERERS.get(element.type, _render_paragraph_html)
    return renderer(element)


def render_html(document: ParsedDocument, title: str | None = None) -> str:
    """Render full document as HTML5 page with Mermaid support."""
    page_title = title or Path(document.source_file).stem
    body_parts = [render_element_html(el) for el in document.elements]
    body_html = "\n".join(part for part in body_parts if part)

    template = _env.get_template("document.html.j2")
    return template.render(title=page_title, body_html=body_html)
