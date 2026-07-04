"""Heuristics for detecting low-quality markdown tables from marker."""

from __future__ import annotations

from doc_converter.utils.markdown_parser import _is_table_separator, _parse_table_row


def table_empty_cell_ratio(table_md: str) -> float:
    """Return fraction of empty cells in a markdown pipe table."""
    rows = [line for line in table_md.splitlines() if line.strip().startswith("|")]
    cells: list[str] = []
    for row in rows:
        if _is_table_separator(row):
            continue
        cells.extend(_parse_table_row(row))
    if not cells:
        return 1.0
    empty_count = sum(1 for cell in cells if not cell.strip())
    return empty_count / len(cells)


def is_low_quality_table(table_md: str) -> bool:
    """Detect marker tables that likely need image-based re-extraction."""
    rows = [line for line in table_md.splitlines() if line.strip().startswith("|")]
    if len(rows) < 2:
        return True

    has_separator = any(_is_table_separator(row) for row in rows)
    if table_empty_cell_ratio(table_md) >= 0.3:
        return True

    pipe_count = sum(row.count("|") for row in rows)
    if not has_separator and pipe_count > len(rows) * 4:
        return True

    column_counts = []
    for row in rows:
        if _is_table_separator(row):
            continue
        column_counts.append(len(_parse_table_row(row)))
    if column_counts and len(set(column_counts)) > 1:
        # ragged rows often indicate broken table OCR
        spread = max(column_counts) - min(column_counts)
        if spread >= 2:
            return True

    return False
