"""Render ParsedDocument to Markdown."""

from doc_converter.ir import DocElement, ParsedDocument


def _render_heading(element: DocElement) -> str:
    level = element.level or 1
    level = max(1, min(level, 6))
    return f"{'#' * level} {element.content}"


def _render_paragraph(element: DocElement) -> str:
    return element.content


def _render_table(element: DocElement) -> str:
    return element.content


def _render_graph(element: DocElement) -> str:
    parts: list[str] = []
    if element.caption:
        parts.append(f"<!-- {element.caption} -->")
    parts.append("```mermaid")
    parts.append(element.content.strip())
    parts.append("```")
    return "\n".join(parts)


def _render_image(element: DocElement) -> str:
    path = element.raw_image_path or element.content
    caption = element.caption or ""
    return f"![{caption}]({path})"


def _render_list(element: DocElement) -> str:
    return element.content


def _render_formula(element: DocElement) -> str:
    return f"$${element.content}$$"


def _render_code(element: DocElement) -> str:
    return f"```\n{element.content}\n```"


_RENDERERS = {
    "heading": _render_heading,
    "paragraph": _render_paragraph,
    "table": _render_table,
    "graph": _render_graph,
    "image": _render_image,
    "list": _render_list,
    "formula": _render_formula,
    "code": _render_code,
}


def render_element(element: DocElement) -> str:
    """Render a single IR element to markdown."""
    renderer = _RENDERERS.get(element.type, _render_paragraph)
    return renderer(element)


def render_markdown(document: ParsedDocument) -> str:
    """Render full document as markdown string."""
    if not document.elements:
        return ""
    chunks = [render_element(el) for el in document.elements]
    return "\n\n".join(chunk for chunk in chunks if chunk)
