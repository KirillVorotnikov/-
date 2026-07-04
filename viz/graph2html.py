#!/usr/bin/env python3
"""
graph2html.py - генерация интерактивной HTML визуализации графа знаний.
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

# CDN URL для библиотек (fallback или при embed_libraries=false)
CDN_URLS = {
    "cytoscape.min.js": "https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.min.js",
    "layout-base.js": "https://unpkg.com/layout-base@2.0.1/layout-base.js",
    "cose-base.js": "https://unpkg.com/cose-base@2.2.0/cose-base.js",
    "cytoscape-cose-bilkent.js": "https://unpkg.com/cytoscape-cose-bilkent@4.1.0/cytoscape-cose-bilkent.js",
    "cytoscape-navigator.js": "https://unpkg.com/cytoscape.js-navigator@2.0.2/cytoscape.js-navigator.js",
    "cytoscape.js-navigator.css": "https://unpkg.com/cytoscape.js-navigator@2.0.2/cytoscape.js-navigator.css",
    "marked.min.js": "https://unpkg.com/marked@14/marked.min.js",
    "mathjax-tex-mml-chtml.js": "https://unpkg.com/mathjax@3/es5/tex-mml-chtml.js",
    "highlight.min.js": "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release/build/highlight.min.js",
    "github-dark.min.css": "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release/build/styles/github-dark.min.css",
}

# Критический порядок загрузки библиотек для cose-bilkent
LIBRARY_ORDER = [
    "cytoscape.min.js",          # 1. Основная библиотека
    "layout-base.js",            # 2. Базовая зависимость
    "cose-base.js",              # 3. Зависимость cose
    "cytoscape-cose-bilkent.js", # 4. Плагин layout
]


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
        raise RuntimeError(f"Cannot load critical library {lib_name}") from e


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


def load_vendor_content(viz_dir, file_list, logger, test_mode=False):
    """Загружает и объединяет vendor файлы с fallback на CDN."""
    content = []
    
    # Обеспечиваем критический порядок загрузки библиотек
    ordered_files = []
    remaining_files = file_list.copy()
    
    # Сначала добавляем библиотеки в критическом порядке
    for lib in LIBRARY_ORDER:
        for file_path in remaining_files:
            if Path(file_path).name == lib:
                ordered_files.append(file_path)
                remaining_files.remove(file_path)
                break
    
    # Добавляем оставшиеся файлы
    ordered_files.extend(remaining_files)
    
    for file_path in ordered_files:
        filename = Path(file_path).name
        full_path = viz_dir / file_path
        
        # Пытаемся загрузить библиотеку (локально или CDN)
        if filename in CDN_URLS:
            try:
                lib_content = get_library_content(filename, full_path, CDN_URLS[filename], logger)
                content.append(f"/* {file_path} */\n{lib_content}")
            except Exception as e:
                logger.error(f"Failed to load critical library {filename}: {e}")
                if filename in LIBRARY_ORDER[:4]:  # Критические библиотеки
                    raise
        else:
            # Некритический файл, пропускаем если не найден
            if full_path.exists():
                try:
                    with open(full_path, encoding="utf-8") as f:
                        content.append(f"/* {file_path} */\n{f.read()}")
                    logger.info(f"Loaded vendor file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to load {file_path}: {e}")
            else:
                logger.warning(f"Vendor file not found: {full_path}")
    
    # Добавляем debug_helpers.js в test режиме
    if test_mode:
        debug_helpers_path = viz_dir / "static" / "debug_helpers.js"
        if debug_helpers_path.exists():
            try:
                with open(debug_helpers_path, encoding="utf-8") as f:
                    debug_content = f.read()
                content.append(f"/* static/debug_helpers.js */\n{debug_content}")
                logger.info("Added debug_helpers.js for test mode")
            except Exception as e:
                logger.warning(f"Failed to load debug_helpers.js: {e}")
    
    return "\n".join(content)


def generate_script_tags(file_list, embed):
    """Генерирует script теги для JS библиотек."""
    if embed:
        return ""  # Контент будет встроен напрямую
    
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
        return ""  # Контент будет встроен напрямую
    
    tags = []
    for file_path in file_list:
        filename = Path(file_path).name
        if filename in CDN_URLS:
            tags.append(f'<link rel="stylesheet" href="{CDN_URLS[filename]}">')
        else:
            tags.append(f'<link rel="stylesheet" href="{file_path}">')
    
    return "\n".join(tags)


def generate_html(graph_data, concepts_data, config, viz_dir, logger, test_mode=False):
    """Генерирует полный HTML файл для production или test режима."""
    # Настройка Jinja2
    template_dir = viz_dir / "templates"
    env = Environment(loader=FileSystemLoader(template_dir), autoescape=False)
    
    # Загрузка шаблонов
    try:
        template = env.get_template("index.html")
        
        css_path = template_dir / "styles.css"
        if css_path.exists():
            with open(css_path, encoding="utf-8") as f:
                styles_content = f.read()
        else:
            logger.warning("styles.css not found, using empty styles")
            styles_content = ""
    
    except Exception as e:
        logger.error(f"Failed to load templates: {e}")
        sys.exit(EXIT_IO_ERROR)
    
    # Подготовка данных
    html_config = config.get("graph2html", {})
    viz_config = config.get("visualization", {})
    colors_config = config.get("colors", {})
    ui_config = config.get("ui", {})
    path_mode_config = config.get("path_mode", {})
    text_formatting_config = config.get(
        "text_formatting",
        {
            "enable_markdown": True,
            "enable_code_highlighting": True,
            "enable_math": True,
            "math_renderer": "mathjax",
        },
    )
    tooltip_config = config.get(
        "tooltip",
        {"max_width": 400, "preview_length": 300, "show_delay": 500, "hide_delay": 200}
    )
    
    minify = html_config.get("minify_json", True)
    embed = html_config.get("embed_libraries", True)
    
    # Извлекаем заголовок из метаданных графа
    graph_title = graph_data.get("_meta", {}).get("title", "Knowledge Graph Visualization")
    
    # Обработка vendor файлов
    vendor_js = html_config.get("vendor_js", [])
    vendor_css = html_config.get("vendor_css", [])
    
    if embed:
        vendor_js_content = load_vendor_content(viz_dir, vendor_js, logger, test_mode)
        vendor_css_content = load_vendor_content(viz_dir, vendor_css, logger, test_mode)
    else:
        vendor_js_content = ""
        vendor_css_content = ""
    
    # Генерация script/link тегов для CDN режима
    script_tags = generate_script_tags(vendor_js, embed)
    link_tags = generate_style_tags(vendor_css, embed)
    
    # Загрузка статических JavaScript модулей
    edge_styles_content = ""
    edge_styles_path = viz_dir / "static" / "edge_styles.js"
    if edge_styles_path.exists():
        try:
            with open(edge_styles_path, encoding="utf-8") as f:
                edge_styles_content = f.read()
            logger.info(f"Loaded edge_styles.js ({len(edge_styles_content)} bytes)")
        except Exception as e:
            logger.warning(f"Failed to load edge_styles.js: {e}")
    else:
        logger.warning("edge_styles.js not found - using default edge styles")
    
    animation_controller_content = ""
    animation_controller_path = viz_dir / "static" / "animation_controller.js"
    if animation_controller_path.exists():
        try:
            with open(animation_controller_path, encoding="utf-8") as f:
                animation_controller_content = f.read()
            logger.info(f"Loaded animation_controller.js ({len(animation_controller_content)} bytes)")
        except Exception as e:
            logger.warning(f"Failed to load animation_controller.js: {e}")
    else:
        logger.warning("animation_controller.js not found - animations disabled")
    
    graph_core_content = ""
    graph_core_path = viz_dir / "static" / "graph_core.js"
    if graph_core_path.exists():
        try:
            with open(graph_core_path, encoding="utf-8") as f:
                graph_core_content = f.read()
            logger.info(f"Loaded graph_core.js ({len(graph_core_content)} bytes)")
        except Exception as e:
            logger.error(f"Failed to load graph_core.js: {e}")
    else:
        logger.error("graph_core.js not found - graph won't be initialized!")
    
    # Загрузка UI модулей
    ui_controls_content = ""
    ui_controls_path = viz_dir / "static" / "ui_controls.js"
    if ui_controls_path.exists():
        try:
            with open(ui_controls_path, encoding="utf-8") as f:
                ui_controls_content = f.read()
            logger.info(f"Loaded ui_controls.js ({len(ui_controls_content)} bytes)")
        except Exception as e:
            logger.warning(f"Failed to load ui_controls.js: {e}")
    else:
        logger.warning("ui_controls.js not found - UI controls disabled")
    
    course_panel_content = ""
    course_panel_path = viz_dir / "static" / "course_panel.js"
    if course_panel_path.exists():
        try:
            with open(course_panel_path, encoding="utf-8") as f:
                course_panel_content = f.read()
            logger.info(f"Loaded course_panel.js ({len(course_panel_content)} bytes)")
        except Exception as e:
            logger.warning(f"Failed to load course_panel.js: {e}")
    else:
        logger.warning("course_panel.js not found - Course panel disabled")
    
    # Загрузка mode модулей
    path_finder_content = ""
    path_finder_path = viz_dir / "static" / "path_finder.js"
    if path_finder_path.exists():
        try:
            with open(path_finder_path, encoding="utf-8") as f:
                path_finder_content = f.read()
            logger.info(f"Loaded path_finder.js ({len(path_finder_content)} bytes)")
        except Exception as e:
            logger.warning(f"Failed to load path_finder.js: {e}")
    else:
        logger.warning("path_finder.js not found - Path Mode disabled")
    
    clusters_bridges_content = ""
    clusters_bridges_path = viz_dir / "static" / "clusters_bridges.js"
    if clusters_bridges_path.exists():
        try:
            with open(clusters_bridges_path, encoding="utf-8") as f:
                clusters_bridges_content = f.read()
            logger.info(f"Loaded clusters_bridges.js ({len(clusters_bridges_content)} bytes)")
        except Exception as e:
            logger.warning(f"Failed to load clusters_bridges.js: {e}")
    else:
        logger.warning("clusters_bridges.js not found - Clusters Mode disabled")
    
    tour_mode_content = ""
    tour_mode_path = viz_dir / "static" / "tour_mode.js"
    if tour_mode_path.exists():
        try:
            with open(tour_mode_path, encoding="utf-8") as f:
                tour_mode_content = f.read()
            logger.info(f"Loaded tour_mode.js ({len(tour_mode_content)} bytes)")
        except Exception as e:
            logger.warning(f"Failed to load tour_mode.js: {e}")
    else:
        logger.warning("tour_mode.js not found - Tour Mode disabled")
    
    # Добавляем скрипт регистрации cose-bilkent
    if embed:
        vendor_js_content += """
// Register cose-bilkent extension
if (typeof cytoscape !== 'undefined' && typeof cytoscapeCoseBilkent !== 'undefined') {
    cytoscape.use(cytoscapeCoseBilkent);
    console.log('✅ cose-bilkent layout registered successfully');
} else {
    console.error('❌ Failed to register cose-bilkent:', {
        cytoscape: typeof cytoscape,
        cytoscapeCoseBilkent: typeof cytoscapeCoseBilkent
    });
}
"""
    else:
        script_tags += """
<script>
// Register cose-bilkent extension
if (typeof cytoscape !== 'undefined' && typeof cytoscapeCoseBilkent !== 'undefined') {
    cytoscape.use(cytoscapeCoseBilkent);
    console.log('✅ cose-bilkent layout registered successfully');
} else {
    console.error('❌ Failed to register cose-bilkent:', {
        cytoscape: typeof cytoscape,
        cytoscapeCoseBilkent: typeof cytoscapeCoseBilkent
    });
}
</script>"""
    
    # Подготовка контекста шаблона
    context = {
        "graph_data_json": minify_json_data(graph_data, minify),
        "concepts_data_json": minify_json_data(concepts_data, minify),
        "title": graph_title,
        "viz_config": viz_config,
        "colors_config": colors_config,
        "ui_config": ui_config,
        "path_mode_config": path_mode_config,
        "text_formatting": text_formatting_config,
        "tooltip_config": tooltip_config,
        "node_shapes": {
            "Chunk": config.get("node_shapes", {}).get("chunk_shape", "hexagon"),
            "Concept": config.get("node_shapes", {}).get("concept_shape", "star"),
            "Assessment": config.get("node_shapes", {}).get("assessment_shape", "roundrectangle"),
        },
        "embed_libraries": embed,
        "vendor_js_content": vendor_js_content,
        "vendor_css_content": vendor_css_content,
        "styles_content": styles_content,
        "edge_styles_content": edge_styles_content if embed else "",
        "animation_controller_content": animation_controller_content if embed else "",
        "graph_core_content": graph_core_content if embed else "",
        "ui_controls_content": ui_controls_content if embed else "",
        "course_panel_content": course_panel_content if embed else "",
        "path_finder_content": path_finder_content if embed else "",
        "clusters_bridges_content": clusters_bridges_content if embed else "",
        "tour_mode_content": tour_mode_content if embed else "",
        "script_tags": script_tags,
        "link_tags": link_tags,
        "graph_stats": {
            "nodes": len(graph_data.get("nodes", [])),
            "edges": len(graph_data.get("edges", [])),
            "concepts": len(concepts_data.get("concepts", [])),
        },
    }
    
    # Рендеринг шаблона
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
            minify_js=False,  # Отключено из-за проблем с встроенным JS
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
        description="Generate HTML visualization of knowledge graph"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Use test data for development (from /viz/data/test/)"
    )
    args = parser.parse_args()
    
    setup_console_encoding()
    
    # Пути
    viz_dir = Path(__file__).parent
    log_file = viz_dir / "logs" / "graph2html.log"
    logger = setup_logging(log_file)
    
    logger.info("=" * 80)
    logger.info(f"Starting HTML generation (mode: {'test' if args.test else 'production'})")
    
    # Загрузка конфигурации
    try:
        config = load_config(viz_dir / "config.toml")
        logger.info("Configuration loaded")
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(EXIT_CONFIG_ERROR)
    
    # Загрузка данных
    data_dir = viz_dir / "data" / "out"
    graph_data, concepts_data = load_graph_data(data_dir, logger, test_mode=args.test)
    
    # Генерация HTML
    html = generate_html(graph_data, concepts_data, config, viz_dir, logger, test_mode=args.test)
    
    # Минификация HTML
    html = minify_html_content(html, config, logger)
    
    # Сохранение
    if args.test:
        output_filename = "test_graph.html"
    else:
        output_filename = config.get("graph2html", {}).get("output_filename", "knowledge_graph.html")
    
    output_path = data_dir / output_filename
    save_html(html, output_path, logger)
    
    logger.info(f"HTML generation completed successfully (mode: {'test' if args.test else 'production'})")
    print(f"\n✓ HTML visualization created: {output_path}")
    
    if args.test:
        print("\nTest mode notes:")
        print("  - Using data from /viz/data/test/tiny_html_data.json")
        print("  - debug_helpers.js included (if available)")
        print("  - Open in browser to test: file://" + str(output_path.absolute()))
    
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())