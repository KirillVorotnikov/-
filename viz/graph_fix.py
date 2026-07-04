#!/usr/bin/env python3
"""
graph_fix.py - маркировка LLM-сгенерированного контента в графе знаний.
Добавляет маркеры [added_by=LLM] к полям, сгенерированным моделью,
и обновляет текстовые поля Concept узлов из ConceptDictionary.
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config import ConfigValidationError, load_config
from src.utils.console_encoding import setup_console_encoding
from src.utils.exit_codes import (
    EXIT_CONFIG_ERROR, EXIT_INPUT_ERROR, EXIT_IO_ERROR,
    EXIT_RUNTIME_ERROR, EXIT_SUCCESS, log_exit,
)
from src.utils.validation import ValidationError, validate_json


def setup_logging(log_file):
    """Настраивает логирование в файл и консоль."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    
    logger = logging.getLogger(__name__)
    logger.info("=" * 80)
    logger.info("Starting graph_fix utility")
    return logger


def load_input_files(data_dir, logger):
    """Загружает ConceptDictionary и LearningChunkGraph из JSON файлов."""
    concepts_file = data_dir / "ConceptDictionary_wow.json"
    graph_file = data_dir / "LearningChunkGraph_wow.json"
    
    # Проверяем существование файлов
    if not concepts_file.exists():
        logger.error(f"Concepts file not found: {concepts_file}")
        log_exit(logger, EXIT_INPUT_ERROR, "Missing ConceptDictionary_wow.json")
        sys.exit(EXIT_INPUT_ERROR)
    
    if not graph_file.exists():
        logger.error(f"Graph file not found: {graph_file}")
        log_exit(logger, EXIT_INPUT_ERROR, "Missing LearningChunkGraph_wow.json")
        sys.exit(EXIT_INPUT_ERROR)
    
    logger.info(f"Loading concepts from: {concepts_file}")
    logger.info(f"Loading graph from: {graph_file}")
    
    try:
        with open(concepts_file, encoding="utf-8") as f:
            concepts_data = json.load(f)
        
        with open(graph_file, encoding="utf-8") as f:
            graph_data = json.load(f)
    
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        log_exit(logger, EXIT_INPUT_ERROR, f"JSON parse error: {e}")
        sys.exit(EXIT_INPUT_ERROR)
    
    except Exception as e:
        logger.error(f"Failed to load files: {e}")
        log_exit(logger, EXIT_IO_ERROR, f"File read error: {e}")
        sys.exit(EXIT_IO_ERROR)
    
    # Валидация структуры
    logger.info("Validating data structure")
    try:
        validate_json(concepts_data, "ConceptDictionary")
        validate_json(graph_data, "LearningChunkGraph")
    except ValidationError as e:
        logger.error(f"Validation failed: {e}")
        log_exit(logger, EXIT_INPUT_ERROR, f"Schema validation error: {e}")
        sys.exit(EXIT_INPUT_ERROR)
    
    # Логируем статистику
    num_concepts = len(concepts_data.get("concepts", []))
    num_nodes = len(graph_data.get("nodes", []))
    num_edges = len(graph_data.get("edges", []))
    
    logger.info(f"Loaded {num_concepts} concepts")
    logger.info(f"Loaded graph: {num_nodes} nodes, {num_edges} edges")
    
    return concepts_data, graph_data


def process_chunk_assessment_definitions(nodes, dry_run, logger):
    """Добавляет маркер [added_by=LLM] к определениям Chunk и Assessment узлов."""
    chunks_marked = 0
    assessments_marked = 0
    examples = []
    
    for node in nodes:
        node_type = node.get("type")
        if node_type not in ["Chunk", "Assessment"]:
            continue
        
        definition = node.get("definition")
        if not definition or definition.strip() == "":
            continue
        
        # Пропускаем уже помеченные
        if definition.startswith("[added_by=LLM]"):
            continue
        
        # Добавляем маркер
        new_definition = f"[added_by=LLM] {definition}"
        
        if node_type == "Chunk":
            chunks_marked += 1
        else:
            assessments_marked += 1
        
        # Собираем примеры для dry-run вывода
        if len(examples) < 5:
            examples.append(
                f"[{node_type}] {node['id']}: "
                f'"{definition[:50]}..." → '
                f'"[added_by=LLM] {definition[:50]}..."'
            )
        
        # Применяем изменение если не dry-run
        if not dry_run:
            node["definition"] = new_definition
    
    return chunks_marked, assessments_marked, examples


ONTOLOGICAL_NODE_TYPES = {
    "Material", "Property", "SynthesisMethod", "CharacterizationMethod",
    "FailureMode", "Mechanism", "Condition", "Application", "Source"
}

def process_concept_text(nodes, concepts_data, dry_run, logger):
    """Обновляет поле name онтологических узлов из ConceptDictionary."""
    concept_map = {}
    for concept in concepts_data.get("concepts", []):
        concept_id = concept.get("concept_id")
        if concept_id:
            concept_map[concept_id] = concept
    
    concepts_updated = 0
    examples = []
    
    for node in nodes:
        node_type = node.get("type")
        if node_type not in ONTOLOGICAL_NODE_TYPES:
            continue
        
        node_id = node.get("id")
        concept = concept_map.get(node_id)
        
        if not concept:
            continue
        
        term = concept.get("term", {})
        primary = term.get("primary", "")
        aliases = term.get("aliases", [])
        
        if aliases:
            new_text = f"{primary} ({', '.join(aliases)})"
        else:
            new_text = primary
        
        # В новой онтологии поле называется 'name', но для совместимости проверяем оба
        old_text = node.get("name", node.get("text", ""))
        
        if new_text != old_text:
            concepts_updated += 1
            if len(examples) < 5:
                examples.append(
                    f"[{node_type}] {node_id}: "
                    f'"{old_text[:50]}..." → "{new_text[:50]}"'
                )
            if not dry_run:
                node["name"] = new_text
    
    return concepts_updated, examples


def process_edge_conditions(edges, dry_run, logger):
    """Добавляет маркер [added_by=LLM] к полю conditions в рёбрах."""
    edges_marked = 0
    examples = []
    
    # Маркеры, которые не нужно трогать
    skip_markers = ["added_by=", "fixed_by=", "auto_generated"]
    
    for edge in edges:
        conditions = edge.get("conditions")
        if not conditions or conditions.strip() == "":
            continue
        
        # Проверяем наличие skip-маркеров
        should_skip = any(marker in conditions for marker in skip_markers)
        if should_skip:
            continue
        
        # Пропускаем уже помеченные
        if conditions.startswith("[added_by=LLM]"):
            continue
        
        # Добавляем маркер
        new_conditions = f"[added_by=LLM] {conditions}"
        edges_marked += 1
        
        # Собираем примеры
        if len(examples) < 5:
            source = edge.get("source", "")
            target = edge.get("target", "")
            edge_type = edge.get("type", "")
            examples.append(
                f"[Edge] {source[:20]}→{target[:20]} ({edge_type}): "
                f'"{conditions[:50]}..." → '
                f'"[added_by=LLM] {conditions[:50]}..."'
            )
        
        # Применяем изменение если не dry-run
        if not dry_run:
            edge["conditions"] = new_conditions
    
    return edges_marked, examples


def update_metadata(graph_data, stats, logger):
    """Обновляет метаданные графа со статистикой исправлений."""
    if "_meta" not in graph_data:
        graph_data["_meta"] = {}
    
    graph_data["_meta"]["graph_fix_applied"] = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chunks_definitions_marked": stats["chunks_marked"],
        "assessments_definitions_marked": stats["assessments_marked"],
        "concepts_text_updated": stats["concepts_updated"],
        "edges_conditions_marked": stats["edges_marked"],
    }
    
    logger.info("Updated metadata with fix statistics")


def save_graph(graph_data, output_file, logger):
    """Сохраняет модифицированный граф в файл."""
    try:
        # Валидация перед сохранением
        validate_json(graph_data, "LearningChunkGraph")
        
        # Сохранение с форматированием
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(graph_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Graph saved to: {output_file}")
    
    except ValidationError as e:
        logger.error(f"Validation failed after modifications: {e}")
        log_exit(logger, EXIT_RUNTIME_ERROR, f"Modified graph invalid: {e}")
        sys.exit(EXIT_RUNTIME_ERROR)
    
    except Exception as e:
        logger.error(f"Failed to save graph: {e}")
        log_exit(logger, EXIT_IO_ERROR, f"Save error: {e}")
        sys.exit(EXIT_IO_ERROR)


def print_dry_run_summary(examples, stats):
    """Выводит сводку изменений в режиме dry-run."""
    print("\n" + "=" * 80)
    print("DRY-RUN MODE - No files were modified")
    print("=" * 80)
    
    # Показываем примеры
    if examples["definitions"]:
        print("\nDefinition changes (showing first 5):")
        for example in examples["definitions"]:
            print(f"  {example}")
    
    if examples["concepts"]:
        print("\nConcept text updates (showing first 5):")
        for example in examples["concepts"]:
            print(f"  {example}")
    
    if examples["conditions"]:
        print("\nEdge condition changes (showing first 5):")
        for example in examples["conditions"]:
            print(f"  {example}")
    
    # Показываем сводку
    print("\nSummary of changes that would be made:")
    print(f"  - Chunk definitions marked: {stats['chunks_marked']}")
    print(f"  - Assessment definitions marked: {stats['assessments_marked']}")
    print(f"  - Concept texts updated: {stats['concepts_updated']}")
    print(f"  - Edge conditions marked: {stats['edges_marked']}")
    print(f"  - Total changes: {sum(stats.values())}")
    print("=" * 80)


def main():
    """Главная точка входа."""
    setup_console_encoding()
    
    # Парсинг аргументов командной строки
    parser = argparse.ArgumentParser(
        description="Mark LLM-generated content in enriched knowledge graph"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show changes without modifying files"
    )
    args = parser.parse_args()
    
    # Пути
    viz_dir = Path(__file__).parent
    log_file = viz_dir / "logs" / "graph_fix.log"
    data_dir = viz_dir / "data" / "out"
    
    # Настройка логирования
    logger = setup_logging(log_file)
    
    if args.dry_run:
        logger.info("Running in DRY-RUN mode")
        print("\n[DRY-RUN MODE] Analyzing changes without modifying files...")
    
    try:
        # Загрузка конфигурации (для валидации)
        config_path = viz_dir / "config.toml"
        load_config(config_path)
        logger.info("Configuration loaded")
    
    except (ConfigValidationError, FileNotFoundError) as e:
        logger.error(f"Failed to load config: {e}")
        log_exit(logger, EXIT_CONFIG_ERROR, str(e))
        sys.exit(EXIT_CONFIG_ERROR)
    
    # Загрузка входных файлов
    concepts_data, graph_data = load_input_files(data_dir, logger)
    
    # Обработка узлов и рёбер
    logger.info("Processing graph nodes and edges")
    
    # Обработка определений Chunk/Assessment
    chunks_marked, assessments_marked, def_examples = process_chunk_assessment_definitions(
        graph_data["nodes"], args.dry_run, logger
    )
    
    # Обработка текстовых полей Concept
    concepts_updated, concept_examples = process_concept_text(
        graph_data["nodes"], concepts_data, args.dry_run, logger
    )
    
    # Обработка условий рёбер
    edges_marked, edge_examples = process_edge_conditions(
        graph_data["edges"], args.dry_run, logger
    )
    
    # Сбор статистики
    stats = {
        "chunks_marked": chunks_marked,
        "assessments_marked": assessments_marked,
        "concepts_updated": concepts_updated,
        "edges_marked": edges_marked,
    }
    
    # Логируем статистику
    logger.info(f"Chunks definitions marked: {chunks_marked}")
    logger.info(f"Assessments definitions marked: {assessments_marked}")
    logger.info(f"Concepts text updated: {concepts_updated}")
    logger.info(f"Edges conditions marked: {edges_marked}")
    logger.info(f"Total changes: {sum(stats.values())}")
    
    if args.dry_run:
        # Показываем dry-run сводку
        examples = {
            "definitions": def_examples,
            "concepts": concept_examples,
            "conditions": edge_examples,
        }
        print_dry_run_summary(examples, stats)
        logger.info("Dry-run completed successfully")
    
    else:
        # Обновляем метаданные
        update_metadata(graph_data, stats, logger)
        
        # Сохраняем модифицированный граф
        output_file = data_dir / "LearningChunkGraph_wow.json"
        save_graph(graph_data, output_file, logger)
        
        # Выводим сводку
        print("\n✓ Graph fix completed successfully")
        print(f"  - Chunks definitions marked: {chunks_marked}")
        print(f"  - Assessment definitions marked: {assessments_marked}")
        print(f"  - Concepts text updated: {concepts_updated}")
        print(f"  - Edge conditions marked: {edges_marked}")
        print(f"  - Total changes: {sum(stats.values())}")
        print(f"  - Output saved to: {output_file}")
        
        logger.info("Graph fix utility completed successfully")
        log_exit(logger, EXIT_SUCCESS)
    
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())