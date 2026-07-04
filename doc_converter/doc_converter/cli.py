"""Typer CLI entry point for doc-convert."""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from doc_converter.config import OutputFormat, Settings, VlmBackendChoice
from doc_converter.metadata import build_metadata, write_metadata
from doc_converter.renderers.html_renderer import render_html
from doc_converter.renderers.markdown_renderer import render_markdown
from doc_converter.router import iter_input_files, parse_file

app = typer.Typer(
    name="doc-convert",
    help="Convert docx/pdf/xlsx/png documents to structured markdown or HTML",
    no_args_is_help=True,
)
console = Console()


class FormatOption(str, Enum):
    md = "md"
    html = "html"
    both = "both"


class VlmBackendOption(str, Enum):
    openai = "openai"
    anthropic = "anthropic"
    local = "local"
    mock = "mock"
    off = "off"


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def _build_settings(
    output_format: FormatOption,
    output_dir: Path,
    vlm_backend: VlmBackendOption,
    ocr_lang: str,
    academic: bool,
) -> Settings:
    return Settings(
        VLM_BACKEND=vlm_backend.value,
        OCR_LANG=ocr_lang,
        output_format=output_format.value,
        output_dir=str(output_dir),
        academic=academic,
    )


def _write_outputs(
    document_path: Path,
    stem: str,
    output_dir: Path,
    output_format: OutputFormat,
    md_text: str,
    html_text: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_format in ("md", "both"):
        (output_dir / f"{stem}.md").write_text(md_text, encoding="utf-8")
    if output_format in ("html", "both"):
        (output_dir / f"{stem}.html").write_text(html_text, encoding="utf-8")


def _process_single_file(
    input_file: Path,
    settings: Settings,
    progress: Progress,
    task_id: int,
) -> tuple[str, str | None]:
    """Process one file; returns (status, error_message)."""
    progress.update(task_id, description=f"Parsing {input_file.name}")
    try:
        document = parse_file(input_file, settings)
        progress.update(task_id, description=f"Rendering {input_file.name}")
        md_text = render_markdown(document)
        html_text = render_html(document)
        stem = input_file.stem
        output_dir = Path(settings.output_dir)
        _write_outputs(
            input_file,
            stem,
            output_dir,
            settings.output_format,
            md_text,
            html_text,
        )
        meta = build_metadata(document, status="success")
        write_metadata(output_dir / f"{stem}.meta.json", meta)
        return "success", None
    except Exception as exc:
        logging.exception("Failed to convert %s", input_file)
        stem = input_file.stem
        output_dir = Path(settings.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        failed_meta = {
            "source_file": input_file.name,
            "source_type": input_file.suffix.lstrip(".").lower(),
            "status": "failed",
            "error": str(exc),
            "elements": [],
        }
        write_metadata(output_dir / f"{stem}.meta.json", failed_meta)
        return "failed", str(exc)


@app.command()
def convert(
    input_path: Path = typer.Argument(..., help="Input file path"),
    output_format: FormatOption = typer.Option(FormatOption.md, "--format", help="Output format"),
    output_dir: Path = typer.Option(Path("./out"), "--output-dir", help="Output directory"),
    vlm_backend: VlmBackendOption = typer.Option(
        VlmBackendOption.openai,
        "--vlm-backend",
        help="Vision-language model backend",
    ),
    ocr_lang: str = typer.Option("ru", "--ocr-lang", help="OCR language(s)"),
    academic: bool = typer.Option(False, "--academic", help="Enable GROBID academic PDF parsing"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Convert a single document to markdown and/or HTML."""
    _configure_logging(verbose)
    if not input_path.is_file():
        console.print(f"[red]Input file not found:[/red] {input_path}")
        raise typer.Exit(code=2)

    settings = _build_settings(output_format, output_dir, vlm_backend, ocr_lang, academic)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("convert", total=1)
        status, error = _process_single_file(input_path, settings, progress, task_id)
        progress.update(task_id, completed=1)

    if status == "failed":
        console.print(f"[red]Conversion failed:[/red] {error}")
        raise typer.Exit(code=3)
    console.print(f"[green]Done:[/green] {output_dir}")


@app.command()
def batch(
    input_dir: Path = typer.Argument(..., help="Input directory"),
    output_format: FormatOption = typer.Option(FormatOption.md, "--format", help="Output format"),
    output_dir: Path = typer.Option(Path("./out"), "--output-dir", help="Output directory"),
    recursive: bool = typer.Option(False, "--recursive", help="Scan subdirectories"),
    vlm_backend: VlmBackendOption = typer.Option(
        VlmBackendOption.openai,
        "--vlm-backend",
        help="Vision-language model backend",
    ),
    ocr_lang: str = typer.Option("ru", "--ocr-lang", help="OCR language(s)"),
    academic: bool = typer.Option(False, "--academic", help="Enable GROBID academic PDF parsing"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Convert all supported documents in a directory."""
    _configure_logging(verbose)
    if not input_dir.is_dir():
        console.print(f"[red]Input directory not found:[/red] {input_dir}")
        raise typer.Exit(code=2)

    settings = _build_settings(output_format, output_dir, vlm_backend, ocr_lang, academic)
    settings.recursive = recursive
    files = iter_input_files(input_dir, recursive=recursive)
    if not files:
        console.print("[yellow]No supported files found[/yellow]")
        raise typer.Exit(code=2)

    failed = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        batch_task = progress.add_task("batch", total=len(files))
        for input_file in files:
            file_task = progress.add_task(input_file.name, total=1)
            status, _ = _process_single_file(input_file, settings, progress, file_task)
            progress.update(file_task, completed=1)
            progress.remove_task(file_task)
            if status == "failed":
                failed += 1
            progress.advance(batch_task)

    if failed:
        console.print(f"[yellow]Completed with {failed} failure(s)[/yellow]")
        raise typer.Exit(code=3)
    console.print(f"[green]Batch done:[/green] {len(files)} file(s) → {output_dir}")


if __name__ == "__main__":
    app()
