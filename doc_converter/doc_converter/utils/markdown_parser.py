"""Parse pandoc-style markdown fragments into IR elements."""

from __future__ import annotations

import html
import re
from re import Pattern

from doc_converter.ir import DocElement

_HEADING_RE: Pattern[str] = re.compile(r"^(#{1,6})\s+(.*)$")
_IMAGE_RE: Pattern[str] = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_LIST_ITEM_RE: Pattern[str] = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    if "-" not in stripped:
        return False
    body = stripped.strip("|").replace("|", "").replace("-", "").replace(":", "").strip()
    return body == ""


def _parse_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_table_to_html(md_table: str) -> str:
    """Convert a markdown pipe table to a simple HTML table."""
    rows = [line for line in md_table.strip().splitlines() if line.strip()]
    if not rows:
        return "<table></table>"

    parsed_rows = [_parse_table_row(row) for row in rows if not _is_table_separator(row)]
    if not parsed_rows:
        return "<table></table>"

    header = parsed_rows[0]
    body = parsed_rows[1:] if len(parsed_rows) > 1 else []

    header_html = "".join(f"<th>{html.escape(cell)}</th>" for cell in header)
    body_html = ""
    for row in body:
        padded = row + [""] * (len(header) - len(row))
        body_html += "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in padded) + "</tr>"

    return (
        '<table style="border-collapse:collapse;width:100%;">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{body_html}</tbody></table>"
    )


def _is_special_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("```"):
        return True
    if _HEADING_RE.match(stripped):
        return True
    if stripped.startswith("|"):
        return True
    if _IMAGE_RE.match(stripped):
        return True
    return _LIST_ITEM_RE.match(line) is not None


def parse_markdown(
    markdown_text: str,
    *,
    extraction_method: str = "pandoc",
) -> list[DocElement]:
    """Convert markdown text produced by pandoc into IR elements."""
    elements: list[DocElement] = []
    lines = markdown_text.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            fence = stripped
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            elements.append(
                DocElement(
                    type="code",
                    content="\n".join(code_lines),
                    extraction_method=extraction_method,
                )
            )
            continue

        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            elements.append(
                DocElement(
                    type="heading",
                    content=heading_match.group(2).strip(),
                    level=level,
                    extraction_method=extraction_method,
                )
            )
            index += 1
            continue

        if stripped.startswith("|"):
            table_lines = [lines[index]]
            index += 1
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            table_md = "\n".join(table_lines)
            elements.append(
                DocElement(
                    type="table",
                    content=table_md,
                    html_content=markdown_table_to_html(table_md),
                    extraction_method=extraction_method,
                    confidence=1.0,
                )
            )
            continue

        image_match = _IMAGE_RE.match(stripped)
        if image_match:
            elements.append(
                DocElement(
                    type="image",
                    content=image_match.group(2).strip(),
                    caption=image_match.group(1).strip() or None,
                    extraction_method=extraction_method,
                )
            )
            index += 1
            continue

        list_match = _LIST_ITEM_RE.match(line)
        if list_match:
            list_lines = [line]
            index += 1
            while index < len(lines):
                next_line = lines[index]
                if not next_line.strip():
                    break
                if _LIST_ITEM_RE.match(next_line):
                    list_lines.append(next_line)
                    index += 1
                    continue
                if next_line.startswith("  ") or next_line.startswith("\t"):
                    list_lines.append(next_line)
                    index += 1
                    continue
                break
            elements.append(
                DocElement(
                    type="list",
                    content="\n".join(item.strip() for item in list_lines),
                    extraction_method=extraction_method,
                )
            )
            continue

        paragraph_lines = [line]
        index += 1
        while index < len(lines) and not _is_special_line(lines[index]):
            paragraph_lines.append(lines[index])
            index += 1
        elements.append(
            DocElement(
                type="paragraph",
                content="\n".join(paragraph_lines).strip(),
                extraction_method=extraction_method,
            )
        )

    return elements
