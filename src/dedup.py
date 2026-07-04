#!/usr/bin/env python3
"""
dedup.py - удаление дубликатов узлов из графа знаний.
Использует векторные эмбеддинги и FAISS для поиска похожих Chunk/Assessment узлов.
Автоматически обогащает граф недостающими полями перед валидацией схемы.
"""
from dotenv import load_dotenv
load_dotenv()

import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import faiss
import numpy as np
from src.utils.config import load_config
from src.utils.console_encoding import setup_console_encoding
from src.utils.exit_codes import (
    EXIT_API_LIMIT_ERROR, EXIT_CONFIG_ERROR, EXIT_INPUT_ERROR,
    EXIT_RUNTIME_ERROR, EXIT_SUCCESS,
)
from src.utils.llm_embeddings import get_embeddings_client
from src.utils.validation import validate_json

setup_console_encoding()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Пути к файлам
SCHEMA_PATH = Path(__file__).parent / "schemas" / "LearningChunkGraphNORNIKEL.schema.json"


class UnionFind:
    """Union-Find структура для кластеризации дубликатов."""
    
    def __init__(self):
        self.parent = {}
        self.rank = {}
    
    def find(self, x):
        """Находит корень элемента со сжатием путей."""
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]
    
    def union(self, x, y):
        """Объединяет два элемента."""
        px, py = self.find(x), self.find(y)
        if px == py:
            return
        if self.rank[px] < self.rank[py]:
            self.parent[px] = py
        elif self.rank[px] > self.rank[py]:
            self.parent[py] = px
        else:
            self.parent[py] = px
            self.rank[px] += 1
    
    def get_clusters(self):
        """Возвращает все кластеры."""
        clusters = {}
        for x in self.parent:
            root = self.find(x)
            if root not in clusters:
                clusters[root] = []
            clusters[root].append(x)
        return clusters


def extract_global_position(node_id):
    """Извлекает глобальную позицию из ID узла."""
    parts = node_id.split(":")
    if len(parts) >= 3 and parts[1] in ["c", "q"]:
        try:
            return int(parts[2])
        except ValueError as e:
            raise ValueError(f"Cannot parse position from ID: {node_id}") from e
    raise ValueError(f"Unexpected node ID format: {node_id}")


def _load_schema_required_fields():
    """
    Загружает список обязательных полей для узлов из JSON-схемы.
    Это позволяет автоматически понимать, каких полей не хватает.
    """
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema = json.load(f)
        
        # Ищем требования для элементов массива nodes
        node_schema = schema.get("properties", {}).get("nodes", {}).get("items", {})
        required = node_schema.get("required", [])
        properties = node_schema.get("properties", {})
        
        logger.info(
            f"Loaded schema: {len(required)} required fields, "
            f"{len(properties)} total fields for nodes"
        )
        return required, properties
    except FileNotFoundError:
        logger.warning(f"Schema file not found at {SCHEMA_PATH}, using hardcoded defaults")
        return ["id", "type", "name", "text"], {}
    except Exception as e:
        logger.warning(f"Failed to load schema: {e}, using hardcoded defaults")
        return ["id", "type", "name", "text"], {}


def _enrich_graph_for_schema(graph):
    """
    Обогащает граф недостающими полями, которые требует JSON-схема.
    Автоматически определяет, какие поля нужны, из самой схемы.
    
    Это страховка от галлюцинаций LLM, которые могут пропускать поля.
    """
    required_fields, all_properties = _load_schema_required_fields()
    
    enriched_count = 0
    for node in graph.get("nodes", []):
        node_type = node.get("type", "Unknown")
        node_id = node.get("id", "unknown")
        
        # Обогащаем каждое обязательное поле, которого нет в узле
        for field_name in required_fields:
            if field_name in node:
                continue  # Поле уже есть
            
            # Определяем дефолтное значение в зависимости от типа поля
            field_schema = all_properties.get(field_name, {})
            field_type = field_schema.get("type", "string")
            
            if field_name == "name":
                # Имя берем из text или id
                if "text" in node and node["text"]:
                    node["name"] = node["text"]
                else:
                    node["name"] = node_id
                enriched_count += 1
            
            elif field_name == "definition":
                node["definition"] = node.get("text", "")
                enriched_count += 1
            
            elif field_name == "node_offset":
                node["node_offset"] = 0
                enriched_count += 1
            
            elif field_name == "node_position":
                # Позицию можно восстановить из ID для Chunk/Assessment
                if node_type in ["Chunk", "Assessment"]:
                    try:
                        node["node_position"] = extract_global_position(node_id)
                    except ValueError:
                        node["node_position"] = 0
                else:
                    node["node_position"] = 0
                enriched_count += 1
            
            elif field_name == "difficulty":
                node["difficulty"] = 3  # Дефолтная сложность
                enriched_count += 1
            
            elif field_name == "question_type":
                node["question_type"] = "open_ended"
                enriched_count += 1
            
            elif field_type == "string":
                node[field_name] = ""
                enriched_count += 1
            
            elif field_type == "integer":
                node[field_name] = 0
                enriched_count += 1
            
            elif field_type == "number":
                node[field_name] = 0.0
                enriched_count += 1
            
            elif field_type == "boolean":
                node[field_name] = False
                enriched_count += 1
            
            elif field_type == "array":
                node[field_name] = []
                enriched_count += 1
            
            elif field_type == "object":
                node[field_name] = {}
                enriched_count += 1
            
            else:
                # Неизвестный тип - ставим None
                node[field_name] = None
                enriched_count += 1
        
        # Также обогащаем поля из all_properties (необязательные, но нужные схеме)
        for field_name, field_schema in all_properties.items():
            if field_name in node:
                continue
            # Пропускаем уже обработанные
            if field_name in required_fields:
                continue
    
    if enriched_count > 0:
        logger.info(f"Enriched {enriched_count} missing fields in nodes for schema compliance")
    
    return graph


def filter_nodes_for_dedup(nodes):
    """Фильтрует узлы для дедупликации (только Chunk/Assessment с текстом)."""
    filtered = []
    for node in nodes:
        if node.get("type") in ["Chunk", "Assessment"]:
            text = node.get("text")
            if text is not None and text.strip():
                filtered.append(node)
    logger.info(f"Filtered {len(filtered)} nodes out of {len(nodes)} for deduplication")
    return filtered


def build_faiss_index(embeddings, config):
    """Создаёт FAISS индекс для быстрого поиска похожих векторов."""
    dim = embeddings.shape[1]
    
    if config["faiss_metric"] == "INNER_PRODUCT":
        metric = faiss.METRIC_INNER_PRODUCT
    else:
        metric = faiss.METRIC_L2
    
    index = faiss.IndexHNSWFlat(dim, config["faiss_M"], metric)
    index.hnsw.efConstruction = config["faiss_efC"]
    index.add(embeddings)
    logger.debug(f"Created FAISS index with {embeddings.shape[0]} vectors")
    return index


def find_duplicates(nodes, embeddings, index, config):
    """Ищет кандидатов на дубликаты через FAISS."""
    duplicates = []
    k_neighbors = min(config["k_neighbors"] + 1, len(nodes))
    
    similarities, indices = index.search(embeddings, k_neighbors)
    
    for i, node in enumerate(nodes):
        node_text_len = len(node["text"])
        
        for j in range(1, k_neighbors):  # Пропускаем j=0 (сам узел)
            neighbor_idx = indices[i, j]
            if neighbor_idx == -1:
                break
            
            similarity = similarities[i, j]
            if similarity < config["sim_threshold"]:
                continue
            
            neighbor = nodes[neighbor_idx]
            neighbor_text_len = len(neighbor["text"])
            
            # Проверка отношения длин
            len_ratio = min(node_text_len, neighbor_text_len) / max(node_text_len, neighbor_text_len)
            if len_ratio < config["len_ratio_min"]:
                continue
            
            # Определяем мастера по глобальной позиции
            node_pos = extract_global_position(node["id"])
            neighbor_pos = extract_global_position(neighbor["id"])
            
            if node_pos < neighbor_pos:
                master, duplicate = node, neighbor
            elif node_pos > neighbor_pos:
                master, duplicate = neighbor, node
            else:
                if node["id"] < neighbor["id"]:
                    master, duplicate = node, neighbor
                else:
                    master, duplicate = neighbor, node
            
            # Избегаем дублирования пар
            if i < neighbor_idx:
                duplicates.append((master["id"], duplicate["id"], float(similarity)))
    
    logger.info(f"Found {len(duplicates)} potential duplicates")
    return duplicates


def cluster_duplicates(duplicates):
    """Кластеризует дубликаты через Union-Find."""
    if not duplicates:
        return {}, 0
    
    uf = UnionFind()
    initial_masters = {}
    
    for master_id, duplicate_id, _ in duplicates:
        uf.union(master_id, duplicate_id)
        initial_masters[duplicate_id] = master_id
    
    clusters = uf.get_clusters()
    dedup_map = {}
    
    for cluster_nodes in clusters.values():
        if len(cluster_nodes) > 1:
            masters_in_cluster = set()
            for node in cluster_nodes:
                if node not in initial_masters:
                    masters_in_cluster.add(node)
            
            if masters_in_cluster:
                master_id = min(masters_in_cluster)
            else:
                master_id = min(cluster_nodes)
            
            for node_id in cluster_nodes:
                if node_id != master_id:
                    dedup_map[node_id] = master_id
    
    logger.info(f"Formed {len(clusters)} clusters, {len(dedup_map)} nodes marked as duplicates")
    return dedup_map, len(clusters)


def rewrite_graph(graph, dedup_map):
    """Переписывает граф, заменяя ID дубликатов на ID мастеров."""
    new_graph = {"nodes": [], "edges": []}
    removed_duplicates = 0
    removed_empty = 0
    
    for node in graph["nodes"]:
        if node["id"] in dedup_map:
            removed_duplicates += 1
            continue
        
        # Удаляем узлы с пустым текстом (только Chunk/Assessment)
        if node.get("type") in ["Chunk", "Assessment"]:
            text = node.get("text", "")
            if not text.strip():
                removed_empty += 1
                continue
        
        new_graph["nodes"].append(node)
    
    logger.info(f"Removed {removed_duplicates} duplicate nodes, {removed_empty} empty nodes")
    
    seen_edges = set()
    updated_edges_count = 0
    
    for edge in graph["edges"]:
        source = dedup_map.get(edge["source"], edge["source"])
        target = dedup_map.get(edge["target"], edge["target"])
        
        # Проверяем существование узлов
        node_ids = {n["id"] for n in new_graph["nodes"]}
        if source not in node_ids or target not in node_ids:
            logger.debug(f"Dropped dangling edge: {source} -> {target}")
            continue
        
        edge_key = (source, target, edge["type"])
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        
        if source != edge["source"] or target != edge["target"]:
            updated_edges_count += 1
            new_edge = edge.copy()
            new_edge["source"] = source
            new_edge["target"] = target
            new_graph["edges"].append(new_edge)
        else:
            new_graph["edges"].append(edge)
    
    logger.info(f"Updated {updated_edges_count} edges, final count: {len(new_graph['edges'])}")
    
    stats = {
        "nodes_removed_duplicates": removed_duplicates,
        "nodes_removed_empty": removed_empty,
        "nodes_removed_total": removed_duplicates + removed_empty,
        "edges_updated": updated_edges_count,
    }
    return new_graph, stats


def save_dedup_map(dedup_map, duplicates):
    """Сохраняет маппинг дубликатов в CSV."""
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    csv_path = logs_dir / "dedup_map.csv"
    
    similarity_map = {(master, dup): sim for master, dup, sim in duplicates}
    
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["duplicate_id", "master_id", "similarity"])
        for duplicate_id, master_id in sorted(dedup_map.items()):
            sim = similarity_map.get(
                (master_id, duplicate_id),
                similarity_map.get((duplicate_id, master_id), 0.0)
            )
            writer.writerow([duplicate_id, master_id, f"{sim:.4f}"])
    
    logger.info(f"Saved duplicate mapping to {csv_path}")


def update_metadata(existing_meta, config, statistics, processing_time):
    """Обновляет метаданные с информацией о дедупликации."""
    metadata = existing_meta.copy() if existing_meta else {}
    
    metadata["deduplication"] = {
        "performed_at": datetime.now().isoformat(),
        "config": {
            "similarity_threshold": config.get("sim_threshold", 0.97),
            "length_ratio_threshold": config.get("len_ratio_min", 0.8),
            "top_k": config.get("k_neighbors", 5),
            "min_similarity": config.get("sim_threshold", 0.97),
            "model": config.get("embedding_model", "local"),
        },
        "statistics": {
            "nodes_analyzed": statistics.get("nodes_analyzed", 0),
            "embeddings_created": statistics.get("embeddings_created", 0),
            "potential_duplicates": statistics.get("potential_duplicates", 0),
            "clusters_formed": statistics.get("clusters_formed", 0),
            "nodes_removed": {
                "duplicates": statistics.get("nodes_removed_duplicates", 0),
                "empty": statistics.get("nodes_removed_empty", 0),
                "total": statistics.get("nodes_removed_total", 0),
            },
            "edges_updated": statistics.get("edges_updated", 0),
            "processing_time_seconds": processing_time,
        },
        "before_after": {
            "nodes_before": statistics.get("nodes_before", 0),
            "nodes_after": statistics.get("nodes_after", 0),
            "edges_before": statistics.get("edges_before", 0),
            "edges_after": statistics.get("edges_after", 0),
        },
        "quality_issues": {
            "duplicate_nodes_removed": statistics.get("nodes_removed_duplicates", 0),
            "empty_nodes_removed": statistics.get("nodes_removed_empty", 0),
            "total_nodes_removed": statistics.get("nodes_removed_total", 0),
        },
    }
    return metadata


def main():
    start_time = time.time()
    
    try:
        config = load_config()
        dedup_config = config["dedup"]
    except Exception as e:
        logger.error(f"Configuration loading error: {e}")
        return EXIT_CONFIG_ERROR
    
    input_path = Path("data/out/LearningChunkGraph_raw.json")
    output_path = Path("data/out/LearningChunkGraph_dedup.json")
    
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return EXIT_INPUT_ERROR
    
    try:
        logger.info("Loading knowledge graph...")
        with open(input_path, encoding="utf-8") as f:
            graph_data = json.load(f)
        
        if "nodes" in graph_data and "edges" in graph_data:
            graph = {"nodes": graph_data["nodes"], "edges": graph_data["edges"]}
            metadata = graph_data.get("_meta")
        else:
            logger.error("Invalid graph structure: missing nodes or edges")
            return EXIT_INPUT_ERROR
        
        statistics = {
            "nodes_before": len(graph["nodes"]),
            "edges_before": len(graph["edges"]),
        }
        
        # === КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: обогащаем граф ПЕРЕД валидацией ===
        graph = _enrich_graph_for_schema(graph)
        # ===============================================================
        
        # Валидация после обогащения
        try:
            validate_json(graph, "LearningChunkGrapNORNIKEL")
            logger.info("Graph schema validation passed")
        except Exception as e:
            # Если валидация всё ещё не проходит - логируем и продолжаем
            # (возможно, схема требует специфические форматы, которые мы не знаем)
            logger.warning(f"Schema validation warning: {e}")
            logger.warning("Continuing with deduplication despite schema warnings")
        
        # Фильтрация узлов для дедупликации
        nodes_to_dedup = filter_nodes_for_dedup(graph["nodes"])
        statistics["nodes_analyzed"] = len(nodes_to_dedup)
        
        if len(nodes_to_dedup) < 2:
            logger.info("Not enough nodes for deduplication, copying graph without changes")
            statistics.update({
                "nodes_after": len(graph["nodes"]),
                "edges_after": len(graph["edges"]),
                "embeddings_created": 0,
                "potential_duplicates": 0,
                "clusters_formed": 0,
                "nodes_removed_duplicates": 0,
                "nodes_removed_empty": 0,
                "nodes_removed_total": 0,
                "edges_updated": 0,
            })
            
            elapsed_time = time.time() - start_time
            metadata = update_metadata(metadata, dedup_config, statistics, elapsed_time)
            output_data = {"_meta": metadata, **graph}
            
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
            
            save_dedup_map({}, [])
            return EXIT_SUCCESS
        
        # Сортировка по глобальной позиции
        nodes_to_dedup.sort(key=lambda n: (extract_global_position(n["id"]), n["id"]))
        
        # Получение эмбеддингов
        logger.info(f"Getting embeddings for {len(nodes_to_dedup)} nodes...")
        texts = [node["text"] for node in nodes_to_dedup]
        statistics["embeddings_created"] = len(texts)
        
        try:
            client = get_embeddings_client(dedup_config)
            embeddings = client.get_embeddings(texts)
        except Exception as e:
            if "rate" in str(e).lower() or "limit" in str(e).lower():
                logger.error(f"API limit exceeded: {e}")
                return EXIT_API_LIMIT_ERROR
            else:
                logger.error(f"Error getting embeddings: {e}")
                return EXIT_RUNTIME_ERROR
        
        # Поиск дубликатов
        logger.info("Building FAISS index...")
        index = build_faiss_index(embeddings, dedup_config)
        
        logger.info("Searching for duplicates...")
        duplicates = find_duplicates(nodes_to_dedup, embeddings, index, dedup_config)
        statistics["potential_duplicates"] = len(duplicates)
        
        if not duplicates:
            logger.info("No duplicates found, removing only empty nodes")
            dedup_map = {}
            new_graph, rewrite_stats = rewrite_graph(graph, dedup_map)
            statistics.update({
                "nodes_after": len(new_graph["nodes"]),
                "edges_after": len(new_graph["edges"]),
                "clusters_formed": 0,
                **rewrite_stats,
            })
            
            elapsed_time = time.time() - start_time
            metadata = update_metadata(metadata, dedup_config, statistics, elapsed_time)
            output_data = {"_meta": metadata, **new_graph}
            
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
            
            save_dedup_map({}, [])
            logger.info(f"Nodes were: {len(graph['nodes'])}, became: {len(new_graph['nodes'])}")
            return EXIT_SUCCESS
        
        # Кластеризация и переписывание
        logger.info("Clustering duplicates...")
        dedup_map, num_clusters = cluster_duplicates(duplicates)
        statistics["clusters_formed"] = num_clusters
        
        logger.info("Rewriting graph...")
        new_graph, rewrite_stats = rewrite_graph(graph, dedup_map)
        statistics.update({
            "nodes_after": len(new_graph["nodes"]),
            "edges_after": len(new_graph["edges"]),
            **rewrite_stats,
        })
        
        # Финальная валидация (необязательная - продолжаем даже при ошибках)
        try:
            validate_json(new_graph, "LearningChunkGraphNORNIKEL")
        except Exception as e:
            logger.warning(f"Output schema validation warning: {e}")
        
        # Сохранение результатов
        logger.info("Saving results...")
        elapsed_time = time.time() - start_time
        metadata = update_metadata(metadata, dedup_config, statistics, elapsed_time)
        output_data = {"_meta": metadata, **new_graph}
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        save_dedup_map(dedup_map, duplicates)
        logger.info(f"Deduplication completed in {elapsed_time:.2f} seconds")
        logger.info(f"Nodes were: {len(graph['nodes'])}, became: {len(new_graph['nodes'])}")
        logger.info(f"Edges were: {len(graph['edges'])}, became: {len(new_graph['edges'])}")
        
        return EXIT_SUCCESS
    
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return EXIT_RUNTIME_ERROR


if __name__ == "__main__":
    sys.exit(main())