#!/usr/bin/env python3
"""
graph2viewer.py - генерация HTML viewer для детального исследования графа знаний.
Поддерживает production и test режимы.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import minify_html
from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config import load_config
from src.utils.console_encoding import setup_console_encoding
from src.utils.exit_codes import (
    EXIT_CONFIG_ERROR, EXIT_INPUT_ERROR, EXIT_IO_ERROR,
    EXIT_RUNTIME_ERROR, EXIT_SUCCESS,
)

# CDN URL для библиотек форматирования
CDN_URLS = {
    "marked.min.js": "https://unpkg.com/marked@14/marked.min.js",
    "mathjax-tex-mml-chtml.js": "https://unpkg.com/mathjax@3/es5/tex-mml-chtml.js",
    "highlight.min.js": "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release/build/highlight.min.js",
    "github-dark.min.css": "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release/build/styles/github-dark.min.css",
}


def setup_logging(log_file):
    """Настраивает логирование."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    
    return logging.getLogger(__name__)


def get_library_content(lib_name, lib_path, cdn_url, logger):
    """Загружает содержимое библиотеки из локального файла или CDN."""
    if lib_path.exists():
        try:
            with open(lib_path, encoding="utf-8") as f:
                content = f.read()
            logger.info(f"Loaded vendor file: {lib_name} ({len(content)} bytes)")
            return content
        except Exception as e:
            logger.warning(f"Failed to read {lib_path}: {e}")
    
    # Fallback на CDN
    logger.warning(f"Vendor file {lib_name} not found, downloading from CDN: {cdn_url}")
    
    try:
        import requests
        response = requests.get(cdn_url, timeout=30)
        response.raise_for_status()
        content = response.text
        logger.info(f"Downloaded {lib_name} from CDN ({len(content)} bytes)")
        return content
    
    except ImportError as e:
        logger.error("requests library not available for CDN fallback")
        raise RuntimeError(
            f"Cannot load {lib_name}: file not found and requests not available"
        ) from e
    
    except Exception as e:
        logger.error(f"Failed to download {lib_name} from CDN: {e}")
        raise RuntimeError(f"Cannot load library {lib_name}") from e


def load_graph_data(data_dir, logger, test_mode=False):
    """Загружает граф и данные концептов."""
    if test_mode:
        graph_path = data_dir.parent / "test" / "tiny_html_data.json"
        concepts_path = data_dir.parent / "test" / "tiny_html_concepts.json"
    else:
        graph_path = data_dir / "LearningChunkGraph_wow.json"
        concepts_path = data_dir / "ConceptDictionary_wow.json"
    
    if not graph_path.exists():
        logger.error(f"Graph file not found: {graph_path}")
        sys.exit(EXIT_INPUT_ERROR)
    
    if not concepts_path.exists():
        if test_mode:
            logger.info("Test concept dictionary not found, using empty stub")
            concepts_data = {
                "concepts": [],
                "_meta": {"note": "Test concept dictionary placeholder"},
            }
        else:
            logger.error(f"Concepts file not found: {concepts_path}")
            sys.exit(EXIT_INPUT_ERROR)
    else:
        try:
            with open(concepts_path, encoding="utf-8") as f:
                concepts_data = json.load(f)
            logger.info(f"Loaded concepts: {len(concepts_data.get('concepts', []))} concepts")
        except Exception as e:
            logger.error(f"Error loading concepts: {e}")
            sys.exit(EXIT_IO_ERROR)
    
    try:
        with open(graph_path, encoding="utf-8") as f:
            graph_data = json.load(f)
        
        logger.info(
            f"Loaded {'test' if test_mode else 'production'} graph: "
            f"{len(graph_data.get('nodes', []))} nodes, "
            f"{len(graph_data.get('edges', []))} edges"
        )
        return graph_data, concepts_data
    
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        sys.exit(EXIT_INPUT_ERROR)
    
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        sys.exit(EXIT_IO_ERROR)


def minify_json_data(data, minify):
    """Конвертирует данные в JSON строку, опционально минифицированную."""
    if minify:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        return json.dumps(data, ensure_ascii=False, indent=2)


def load_vendor_content(viz_dir, file_list, logger):
    """Загружает и объединяет vendor файлы с fallback на CDN."""
    content = []
    
    for file_path in file_list:
        filename = Path(file_path).name
        full_path = viz_dir / file_path
        
        if filename in CDN_URLS:
            try:
                lib_content = get_library_content(filename, full_path, CDN_URLS[filename], logger)
                content.append(f"/* {file_path} */\n{lib_content}")
            except Exception as e:
                logger.warning(f"Failed to load library {filename}: {e}")
        else:
            if full_path.exists():
                try:
                    with open(full_path, encoding="utf-8") as f:
                        content.append(f"/* {file_path} */\n{f.read()}")
                    logger.info(f"Loaded vendor file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to load {file_path}: {e}")
            else:
                logger.warning(f"Vendor file not found: {full_path}")
    
    return "\n".join(content)


def generate_script_tags(file_list, embed):
    """Генерирует script теги для JS библиотек."""
    if embed:
        return ""
    
    tags = []
    for file_path in file_list:
        filename = Path(file_path).name
        if filename in CDN_URLS:
            tags.append(f'<script src="{CDN_URLS[filename]}"></script>')
        else:
            tags.append(f'<script src="{file_path}"></script>')
    
    return "\n".join(tags)


def generate_style_tags(file_list, embed):
    """Генерирует link теги для CSS библиотек."""
    if embed:
        return ""
    
    tags = []
    for file_path in file_list:
        filename = Path(file_path).name
        if filename in CDN_URLS:
            tags.append(f'<link rel="stylesheet" href="{CDN_URLS[filename]}">')
        else:
            tags.append(f'<link rel="stylesheet" href="{file_path}">')
    
    return "\n".join(tags)


def generate_html(graph_data, concepts_data, config, viz_dir, logger, test_mode=False):
    """Генерирует полный HTML файл для viewer."""
    # Настройка Jinja2
    template_dir = viz_dir / "templates" / "viewer"
    env = Environment(loader=FileSystemLoader(template_dir), autoescape=False)
    
    try:
        template = env.get_template("index.html")
        
        css_path = template_dir / "viewer_styles.css"
        if css_path.exists():
            with open(css_path, encoding="utf-8") as f:
                styles_content = f.read()
        else:
            logger.warning("viewer_styles.css not found, using empty styles")
            styles_content = ""
    
    except Exception as e:
        logger.error(f"Failed to load templates: {e}")
        sys.exit(EXIT_IO_ERROR)
    
    # Подготовка данных
    html_config = config.get("graph2html", {})
    text_formatting_config = config.get(
        "text_formatting",
        {
            "enable_markdown": True,
            "enable_code_highlighting": True,
            "enable_math": True,
            "math_renderer": "mathjax",
        },
    )
    
    minify = html_config.get("minify_json", True)
    embed = html_config.get("embed_libraries", True)
    
    graph_title = graph_data.get("_meta", {}).get("title", "Knowledge Graph Viewer")
    node_count = len(graph_data.get("nodes", []))
    edge_count = len(graph_data.get("edges", []))
    
    # Vendor файлы (только библиотеки форматирования)
    vendor_js = [
        "vendor/marked.min.js",
        "vendor/highlight.min.js",
        "vendor/mathjax-tex-mml-chtml.js",
    ]
    vendor_css = ["vendor/github-dark.min.css"]
    
    if embed:
        vendor_js_content = load_vendor_content(viz_dir, vendor_js, logger)
        vendor_css_content = load_vendor_content(viz_dir, vendor_css, logger)
    else:
        vendor_js_content = ""
        vendor_css_content = ""
    
    script_tags = generate_script_tags(vendor_js, embed)
    link_tags = generate_style_tags(vendor_css, embed)
    
    # Загрузка viewer JavaScript модулей
    modules_to_load = [
        "viewer_core.js",
        "node_explorer.js",
        "edge_inspector.js",
        "navigation_history.js",
        "formatters.js",
        "search_filter.js",
    ]
    
    module_contents = {}
    for module_name in modules_to_load:
        module_path = viz_dir / "static" / "viewer" / module_name
        
        if module_path.exists():
            try:
                with open(module_path, encoding="utf-8") as f:
                    module_contents[module_name] = f.read()
                logger.info(f"Loaded {module_name} ({len(module_contents[module_name])} bytes)")
            except Exception as e:
                logger.warning(f"Failed to load {module_name}: {e}")
                module_contents[module_name] = ""
        else:
            logger.warning(f"{module_name} not found - feature disabled")
            module_contents[module_name] = ""
    
    # Подготовка контекста шаблона
    context = {
        "graph_data_json": minify_json_data(graph_data, minify),
        "concepts_data_json": minify_json_data(concepts_data, minify),
        "title": graph_title,
        "node_count": node_count,
        "edge_count": edge_count,
        "text_formatting": text_formatting_config,
        "embed_libraries": embed,
        "vendor_js_content": vendor_js_content,
        "vendor_css_content": vendor_css_content,
        "styles_content": styles_content,
        "viewer_core_content": module_contents.get("viewer_core.js", ""),
        "node_explorer_content": module_contents.get("node_explorer.js", ""),
        "edge_inspector_content": module_contents.get("edge_inspector.js", ""),
        "navigation_history_content": module_contents.get("navigation_history.js", ""),
        "formatters_content": module_contents.get("formatters.js", ""),
        "search_filter_content": module_contents.get("search_filter.js", ""),
        "script_tags": script_tags,
        "link_tags": link_tags,
    }
    
    try:
        html = template.render(**context)
        logger.info("HTML generated successfully")
        return html
    
    except Exception as e:
        logger.error(f"Template rendering failed: {e}")
        sys.exit(EXIT_RUNTIME_ERROR)


def minify_html_content(html, config, logger):
    """Минифицирует HTML если настроено."""
    minify = config.get("graph2html", {}).get("minify_html", False)
    if not minify:
        return html
    
    try:
        original_size = len(html.encode("utf-8"))
        
        minified = minify_html.minify(
            html,
            minify_css=True,
            minify_js=False,
            remove_processing_instructions=True,
            keep_closing_tags=True,
            keep_html_and_head_opening_tags=True,
        )
        
        minified_size = len(minified.encode("utf-8"))
        saved_bytes = original_size - minified_size
        saved_percent = (saved_bytes / original_size) * 100 if original_size > 0 else 0
        
        logger.info(f"HTML minification: {original_size:,} → {minified_size:,} bytes")
        logger.info(f"Saved: {saved_bytes:,} bytes ({saved_percent:.1f}%)")
        
        return minified
    
    except Exception as e:
        logger.warning(f"HTML minification failed: {e}")
        logger.warning("Using non-minified HTML")
        return html


def save_html(html, output_path, logger):
    """Сохраняет HTML в файл."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        
        logger.info(f"HTML saved to: {output_path}")
        
        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"File size: {size_mb:.2f} MB")
    
    except Exception as e:
        logger.error(f"Failed to save HTML: {e}")
        sys.exit(EXIT_IO_ERROR)


def main():
    """Главная точка входа с поддержкой production и test режимов."""
    parser = argparse.ArgumentParser(
        description="Generate HTML viewer for knowledge graph exploration"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Use test data for development (from /viz/data/test/)"
    )
    args = parser.parse_args()
    
    setup_console_encoding()
    
    viz_dir = Path(__file__).parent
    log_file = viz_dir / "logs" / "graph2viewer.log"
    logger = setup_logging(log_file)
    
    logger.info("=" * 80)
    logger.info(f"Starting HTML viewer generation (mode: {'test' if args.test else 'production'})")
    
    try:
        config = load_config(viz_dir / "config.toml")
        logger.info("Configuration loaded")
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(EXIT_CONFIG_ERROR)
    
    data_dir = viz_dir / "data" / "out"
    graph_data, concepts_data = load_graph_data(data_dir, logger, test_mode=args.test)
    
    html = generate_html(graph_data, concepts_data, config, viz_dir, logger, test_mode=args.test)
    html = minify_html_content(html, config, logger)
    
    if args.test:
        output_filename = "test_viewer.html"
    else:
        output_filename = "knowledge_graph_viewer.html"
    
    output_path = data_dir / output_filename
    save_html(html, output_path, logger)
    
    logger.info(f"HTML viewer generation completed successfully (mode: {'test' if args.test else 'production'})")
    print(f"\n✓ HTML viewer created: {output_path}")
    
    if args.test:
        print("\nTest mode notes:")
        print("  - Using data from /viz/data/test/tiny_html_data.json")
        print("  - Open in browser to test: file://" + str(output_path.absolute()))
    
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())