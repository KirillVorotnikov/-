#!/usr/bin/env python
"""
refiner_longrange.py - добавление дальнодействующих связей в граф знаний.
Ищет пропущенные связи между узлами, которые не попали в один контекст.
Использует семантическую схожесть (FAISS) для поиска кандидатов и LLM для анализа.
"""
from dotenv import load_dotenv
load_dotenv()

import json
import logging
import shutil
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
import faiss
import numpy as np

from src.utils.llm_embeddings import get_embeddings_client
from src.utils.config import load_config
from src.utils.console_encoding import setup_console_encoding
from src.utils.exit_codes import (
    EXIT_API_LIMIT_ERROR, EXIT_CONFIG_ERROR, EXIT_INPUT_ERROR,
    EXIT_IO_ERROR, EXIT_RUNTIME_ERROR, EXIT_SUCCESS,
)
from src.utils.llm_providers import LLMClientFactory
from src.utils.validation import validate_graph_invariants, validate_json
from src.utils.ontology_config import ONTOLOGY_CONSTRAINTS

setup_console_encoding()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("refiner")

def setup_json_logging(config):
    """Настраивает JSON Lines логирование для refiner."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = log_dir / f"refiner_longrange_{timestamp}.log"
    
    class JSONLineFormatter(logging.Formatter):
        def format(self, record):
            log_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "event": getattr(record, "event", "log")
            }
            for key in ["slice_id", "node_id", "concept_id", "action", "source", 
                        "target", "type", "weight", "conditions", "pairs_count", 
                        "tokens_used", "duration_ms", "edges_added", "error"]:
                if hasattr(record, key):
                    log_data[key] = getattr(record, key)
            if record.getMessage():
                log_data["message"] = record.getMessage()
            if record.levelname == "DEBUG":
                for key in ["prompt", "response", "raw_response", "new_aliases", 
                            "old_len", "new_len", "similarity"]:
                    if hasattr(record, key):
                        log_data[key] = getattr(record, key)
            return json.dumps(log_data, ensure_ascii=False)
    
    refiner_logger = logging.getLogger("refiner")
    refiner_logger.setLevel(logging.DEBUG if config.get("log_level", "info").lower() == "debug" else logging.INFO)
    refiner_logger.handlers = []
    
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(JSONLineFormatter())
    refiner_logger.addHandler(file_handler)
    
    refiner_logger.info("Refiner started", extra={
        "event": "refiner_longrange_start",
        "config": {
            "model": config["model"],
            "tpm_limit": config["tpm_limit"],
            "sim_threshold": config["sim_threshold"],
            "max_pairs_per_node": config["max_pairs_per_node"]
        }
    })
    return refiner_logger


def log_edge_operation(logger, operation, edge, **kwargs):
    """Логирует операции над ребрами в структурированном формате."""
    extra = {
        "event": f"edge_{operation}",
        "source": edge.get("source"),
        "target": edge.get("target"),
        "type": edge.get("type"),
        "weight": edge.get("weight")
    }
    extra.update(kwargs)
    message = f"Edge {operation}: {edge.get('source')} -> {edge.get('target')} ({edge.get('type')})"
    if operation in ["updated", "replaced"]:
        logger.info(message, extra=extra)
    else:
        logger.debug(message, extra=extra)


def validate_refiner_longrange_config(config):
    """Валидирует параметры конфигурации refiner."""
    required = [
        "embedding_model", "sim_threshold", "max_pairs_per_node",
        "model", "api_key", "tpm_limit", "max_completion", "faiss_M", "faiss_metric"
    ]
    for param in required:
        if param not in config:
            raise ValueError(f"Missing required parameter: {param}")
    
    if not config["api_key"].strip():
        raise ValueError("api_key cannot be empty")
    if not 0 <= config["sim_threshold"] <= 1:
        raise ValueError(f"sim_threshold must be in [0,1], got {config['sim_threshold']}")
    if config["max_pairs_per_node"] <= 0:
        raise ValueError(f"max_pairs_per_node must be > 0, got {config['max_pairs_per_node']}")
    if config["faiss_M"] <= 0:
        raise ValueError(f"faiss_M must be > 0, got {config['faiss_M']}")
    if config["faiss_metric"] not in ["INNER_PRODUCT", "L2"]:
        raise ValueError(f"faiss_metric must be INNER_PRODUCT or L2, got {config['faiss_metric']}")


def load_and_validate_graph(input_path):
    """Загружает граф и проверяет его структуру. Ошибки схемы теперь не ломают пайплайн."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    
    with open(input_path, encoding="utf-8") as f:
        graph_data = json.load(f)
        
    if "nodes" in graph_data and "edges" in graph_data:
        graph = graph_data
    else:
        raise ValueError("Invalid graph structure: missing nodes or edges")
    
    # Мягкая валидация: если схема не совпадает (например, LLM добавила KPI_Target),
    # мы логируем предупреждение, но НЕ прерываем выполнение.
    try:
        validate_json({"nodes": graph["nodes"], "edges": graph["edges"]}, "LearningChunkGraphNORNIKEL")
    except Exception as e:
        logger.warning(f"Schema validation warning: {e}")
        logger.warning("Continuing refiner despite schema mismatches. Custom node types are allowed.")
        
    return graph


def extract_target_nodes(graph):
    """Извлекает узлы для анализа связей (поддержка новой онтологии)."""
    target_types = {
        "Material", "Property", "SynthesisMethod", "CharacterizationMethod",
        "FailureMode", "Mechanism", "Condition", "Application", "Source",
        "Equipment", "BusinessMetric", "InternalExperiment", "Constraint", 
        "HypothesisRecord", "KPI_Target", "Concept",
        "Chunk", "Assessment" # Fallback для совместимости
    }
    return [node for node in graph.get("nodes", []) if node.get("type") in target_types]


def build_edges_index(graph):
    """Строит индекс существующих ребер для быстрого поиска."""
    edges_index = {}
    for edge in graph.get("edges", []):
        source = edge["source"]
        target = edge["target"]
        if source not in edges_index:
            edges_index[source] = {}
        if target not in edges_index[source]:
            edges_index[source][target] = []
        edges_index[source][target].append(edge)
    return edges_index


def get_node_embeddings(nodes, config, logger):
    """Получает эмбеддинги для всех целевых узлов."""
    logger.info(f"Getting embeddings for {len(nodes)} nodes")
    texts = []
    node_ids = []
    for node in nodes:
        if node.get("text", "").strip():
            texts.append(node["text"])
            node_ids.append(node["id"])
        else:
            logger.warning(f"Node {node['id']} has empty text, skipping")
    if not texts:
        logger.error("No texts to get embeddings for")
        return {}
    try:
        client = get_embeddings_client(config)
        embeddings = client.get_embeddings(texts)
        embeddings_dict = {}
        for i, node_id in enumerate(node_ids):
            embeddings_dict[node_id] = embeddings[i]
        logger.info(f"Successfully obtained embeddings for {len(embeddings_dict)} nodes")
        return embeddings_dict
    except Exception as e:
        logger.error(f"Failed to get embeddings: {e}")
        raise


def build_similarity_index(embeddings_dict, nodes, config, logger):
    """Строит FAISS индекс для поиска похожих узлов."""
    # Для онтологических узлов используем сортировку по ID для детерминизма,
    # так как семантические ID (mat_..., prop_...) не содержат token positions.
    sorted_nodes = sorted(nodes, key=lambda n: n["id"])
    
    embeddings_list = []
    node_ids_list = []
    for node in sorted_nodes:
        if node["id"] in embeddings_dict:
            embeddings_list.append(embeddings_dict[node["id"]])
            node_ids_list.append(node["id"])
            
    if not embeddings_list:
        raise ValueError("No embeddings to build index")
        
    embeddings_matrix = np.array(embeddings_list, dtype=np.float32)
    dim = embeddings_matrix.shape[1]
    logger.info(f"Building FAISS index: dim={dim}, M={config['faiss_M']}, metric={config['faiss_metric']}")
    
    if config["faiss_metric"] == "INNER_PRODUCT":
        metric = faiss.METRIC_INNER_PRODUCT
    else:
        metric = faiss.METRIC_L2
        
    index = faiss.IndexHNSWFlat(dim, config["faiss_M"], metric)
    index.hnsw.efConstruction = config.get("faiss_efC", 200)
    index.add(embeddings_matrix)
    logger.info(f"FAISS index built with {index.ntotal} vectors")
    return index, node_ids_list


def generate_candidate_pairs(nodes, embeddings_dict, index, node_ids_list, edges_index, config, logger, pass_direction="forward"):
    """Генерирует пары узлов-кандидатов для анализа связей."""
    nodes_by_id = {node["id"]: node for node in nodes}
    
    # Создаем карту порядка узлов для определения "forward" / "backward"
    node_order = {node_id: i for i, node_id in enumerate(node_ids_list)}
    
    k_neighbors = min(config["max_pairs_per_node"] + 1, len(nodes))
    sim_threshold = config["sim_threshold"]
    if pass_direction == "backward":
        sim_threshold = config.get("backward_sim_threshold", sim_threshold)
        
    candidate_pairs = []
    processed_pairs = set()
    
    logger.info(f"Searching for candidates: k={k_neighbors - 1}, threshold={sim_threshold}")
    
    for i, node_id_a in enumerate(node_ids_list):
        node_a = nodes_by_id[node_id_a]
        embedding_a = embeddings_dict[node_id_a]
        
        query = np.array([embedding_a], dtype=np.float32)
        similarities, indices = index.search(query, k_neighbors)
        
        candidates_for_a = []
        for j, (sim, idx) in enumerate(zip(similarities[0], indices[0])):
            if idx == i:
                continue
            if sim < sim_threshold:
                continue
                
            node_id_b = node_ids_list[idx]
            node_b = nodes_by_id[node_id_b]
            
            # Используем индекс в списке вместо парсинга ID
            position_a = node_order[node_id_a]
            position_b = node_order[node_id_b]
            
            if pass_direction == "forward":
                if position_a >= position_b:
                    continue
            elif pass_direction == "backward":
                if position_a <= position_b:
                    continue
                    
            pair_key = (node_id_a, node_id_b)
            if pair_key in processed_pairs:
                continue
            processed_pairs.add(pair_key)
            
            existing_edges = []
            if node_id_a in edges_index and node_id_b in edges_index[node_id_a]:
                existing_edges.extend(edges_index[node_id_a][node_id_b])
            if node_id_b in edges_index and node_id_a in edges_index[node_id_b]:
                existing_edges.extend(edges_index[node_id_b][node_id_a])
                
            # Fallback на definition, если text отсутствует (для Concept узлов)
            text_b = node_b.get("text") or node_b.get("definition") or node_b.get("name") or ""
            
            candidates_for_a.append({
                "node_id": node_id_b,
                "text": text_b,
                "similarity": float(sim),
                "existing_edges": existing_edges
            })
            
        candidates_for_a.sort(key=lambda x: x["similarity"], reverse=True)
        candidates_for_a = candidates_for_a[:config["max_pairs_per_node"]]
        
        if candidates_for_a:
            text_a = node_a.get("text") or node_a.get("definition") or node_a.get("name") or ""
            candidate_pairs.append({
                "source_node": {"id": node_id_a, "text": text_a},
                "candidates": candidates_for_a
            })
            
        if (i + 1) % 10 == 0:
            logger.debug(f"Processed {i + 1}/{len(node_ids_list)} nodes")
            
    logger.info(f"Generated {len(candidate_pairs)} nodes with candidates, total {sum(len(p['candidates']) for p in candidate_pairs)} pairs")
    return candidate_pairs


def load_refiner_longrange_prompt(config, pass_direction="forward"):
    """Загружает промпт для конкретного направления прохода."""
    if pass_direction == "forward":
        prompt_file = "refiner_longrange_fw.md"
    elif pass_direction == "backward":
        prompt_file = "refiner_longrange_bw.md"
    else:
        raise ValueError(f"Invalid pass_direction: {pass_direction}")
    
    prompt_path = Path(__file__).parent / "prompts" / prompt_file
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    with open(prompt_path, encoding="utf-8") as f:
        return f.read()


def analyze_candidate_pairs(candidate_pairs, graph, config, logger, pass_direction="forward"):
    """Анализирует пары кандидатов через LLM."""
    prompt = load_refiner_longrange_prompt(config, pass_direction)
    logger.info(f"Loaded refiner prompt for {pass_direction} pass")
    start_time = time.time()
    # Используем фабрику для создания клиента (OpenRouter, RouterAI или Local)
    llm_client = LLMClientFactory.create_client(config)
    
    all_new_edges = []
    previous_response_id = None
    api_usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    
    utc3_tz = timezone(timedelta(hours=3))
    start_timestamp = datetime.now(utc3_tz).strftime("%H:%M:%S")
    print(f"[{start_timestamp}] START    | {len(candidate_pairs)} nodes | model={config['model']} | tpm={config['tpm_limit'] // 1000}k")
    logger.info(f"Starting LLM analysis of {len(candidate_pairs)} nodes")
    
    for i, pair_data in enumerate(candidate_pairs):
        source_node = pair_data["source_node"]
        candidates = pair_data["candidates"]
        input_data = {"source_node": source_node, "candidates": candidates}
        
        request_start = time.time()
        max_retries = config.get("max_retries", 3)
        last_error_type = None
        edges_response = None
        
        for attempt in range(max_retries + 1):
            try:
                if attempt == 0:
                    response_text, response_id, usage = llm_client.create_response(
                        instructions=prompt,
                        input_data=json.dumps(input_data, ensure_ascii=False, indent=2),
                        previous_response_id=previous_response_id
                    )
                else:
                    node_timestamp = datetime.now(utc3_tz).strftime("%H:%M:%S")
                    pass_prefix = "F" if pass_direction == "forward" else "B"
                    print(f"[{node_timestamp}] REPAIR   | 🔧 {pass_prefix}{i + 1:03d} Attempt {attempt}/{max_retries} after {last_error_type}...")
                    
                    repair_hint = ""
                    if last_error_type == "json":
                        repair_hint = "\nPLEASE RETURN ONLY VALID JSON ARRAY, NO OTHER TEXT."
                    elif last_error_type == "timeout":
                        repair_hint = "\nBE CONCISE. Focus on important edges."
                    
                    response_text, response_id, usage = llm_client.repair_response(
                        instructions=prompt + repair_hint,
                        input_data=json.dumps(input_data, ensure_ascii=False, indent=2)
                    )
                
                api_usage["requests"] += 1
                api_usage["input_tokens"] += usage.input_tokens
                api_usage["output_tokens"] += usage.output_tokens
                
                edges_response = json.loads(response_text)
                llm_client.confirm_response()
                previous_response_id = response_id
                break
                
            except json.JSONDecodeError as e:
                last_error_type = "json"
                if attempt == max_retries:
                    logger.error(f"Failed to parse JSON for node {source_node['id']} after {max_retries} retries: {e}")
                    bad_response_path = Path(f"logs/{source_node['id']}_bad.json")
                    bad_response_path.parent.mkdir(exist_ok=True)
                    with open(bad_response_path, "w", encoding="utf-8") as f:
                        json.dump({"node_id": source_node["id"], "response": response_text, "error": str(e)}, f, ensure_ascii=False, indent=2)
                    return all_new_edges, api_usage
                continue
                
            except TimeoutError as e:
                last_error_type = "timeout"
                if attempt == max_retries:
                    return all_new_edges, api_usage
                time.sleep(30 * (attempt + 1))
                
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "rate limit" in error_str.lower():
                    last_error_type = "rate_limit"
                    if attempt == max_retries:
                        return all_new_edges, api_usage
                    time.sleep(30 * (attempt + 1))
                else:
                    logger.error(f"Unexpected error processing node {source_node['id']}: {e}")
                    return all_new_edges, api_usage
        
        if edges_response is not None:
            valid_edges = validate_llm_edges(edges_response, source_node["id"], candidates, graph, logger)
            all_new_edges.extend(valid_edges)
            
            added_count = len([e for e in valid_edges if e.get("type")])
            request_time_ms = int((time.time() - request_start) * 1000)
            node_timestamp = datetime.now(utc3_tz).strftime("%H:%M:%S")
            pass_prefix = "F" if pass_direction == "forward" else "B"
            print(f"[{node_timestamp}] NODE     | ✅ {pass_prefix}{i + 1:03d}/{len(candidate_pairs):03d} | pairs={len(candidates)} | tokens={usage.total_tokens} | {request_time_ms}ms | edges_added={added_count}")
    
    end_time = time.time()
    elapsed = int(end_time - start_time)
    minutes, seconds = divmod(elapsed, 60)
    end_timestamp = datetime.now(utc3_tz).strftime("%H:%M:%S")
    total_added = len([e for e in all_new_edges if e.get("type")])
    print(f"[{end_timestamp}] END      | Done | nodes={len(candidate_pairs)} | edges_added={total_added} | time={minutes}m {seconds}s")
    
    api_usage["total_tokens"] = api_usage["input_tokens"] + api_usage["output_tokens"]
    return all_new_edges, api_usage


def validate_llm_edges(edges_response, source_id, candidates, graph, logger):
    candidate_ids = {c["node_id"] for c in candidates}
    valid_edge_types = {
        "IMPROVES", "DEGRADES", "CAUSES", "MITIGATES", "REQUIRES_CONDITION",
        "SYNTHESIZED_BY", "CHARACTERIZED_BY", "HAS_FAILURE_MODE", "APPLIED_IN",
        "SUPPORTED_BY", "SUBCLASS_OF"
    }
    node_registry = {n["id"]: n["type"] for n in graph.get("nodes", [])}
    valid_edges = []
    
    if not isinstance(edges_response, list): return []
        
    for edge_data in edges_response:
        if edge_data.get("type") is None: continue
        source, target, edge_type = edge_data.get("source"), edge_data.get("target"), edge_data.get("type")
        
        if not all([source, target, edge_type]): continue
        if source != source_id or target not in candidate_ids or source == target: continue
        if edge_type not in valid_edge_types: continue
        
        s_type, t_type = node_registry.get(source), node_registry.get(target)
        if s_type and t_type:
            constraints = ONTOLOGY_CONSTRAINTS.get(edge_type)
            if constraints:
                if s_type not in constraints["domain"] or t_type not in constraints["range"]: continue
                if edge_type == "SUBCLASS_OF" and s_type != t_type: continue
                
        attrs = edge_data.get("attributes", {})
        if not isinstance(attrs, dict): attrs = {}
        if "confidence_score" not in attrs:
            attrs["confidence_score"] = edge_data.get("weight", 0.5)
            
        valid_edges.append({"source": source, "target": target, "type": edge_type, "attributes": attrs})
    return valid_edges


def update_graph_with_new_edges(graph, new_edges, logger):
    """
    Обновляет граф новыми ребрами от LLM с учетом новой онтологии (блок attributes).
    
    Args:
        graph: Исходный граф знаний.
        new_edges: Список новых ребер, прошедших валидацию (от validate_llm_edges).
        logger: Логгер для отслеживания изменений.
        
    Returns:
        Словарь со статистикой изменений (added, updated, replaced и т.д.).
    """
    stats = {
        "added": 0,
        "updated": 0,
        "replaced": 0,
        "self_loops_removed": 0,
        "total_processed": 0,
        "types_added": {},
        "types_updated": {},
        "types_replaced": {}
    }
    
    # Индекс существующих ребер: {(source, target): [index1, index2, ...]}
    edge_index = {}
    for i, edge in enumerate(graph.get("edges", [])):
        key = (edge.get("source"), edge.get("target"))
        if key not in edge_index:
            edge_index[key] = []
        edge_index[key].append(i)

    for new_edge in new_edges:
        stats["total_processed"] += 1
        
        source = new_edge.get("source")
        target = new_edge.get("target")
        edge_type = new_edge.get("type")
        
        # Извлекаем confidence_score из блока attributes (новая онтология)
        new_attrs = new_edge.get("attributes", {})
        if not isinstance(new_attrs, dict):
            new_attrs = {}
            
        new_confidence = new_attrs.get("confidence_score", new_edge.get("weight", 0.5))
        try:
            new_confidence = float(new_confidence)
        except (ValueError, TypeError):
            new_confidence = 0.5
            
        key = (source, target)

        # ==========================================
        # Сценарий 1: Ребра между такими узлами еще не существует
        # ==========================================
        if key not in edge_index:
            final_edge = {
                "source": source,
                "target": target,
                "type": edge_type,
                "attributes": new_attrs
            }
            # Помечаем, что ребро добавлено refiner'ом (для отладки и прозрачности)
            final_edge["attributes"]["added_by"] = "refiner_longrange_v2"
            
            graph["edges"].append(final_edge)
            stats["added"] += 1
            stats["types_added"][edge_type] = stats["types_added"].get(edge_type, 0) + 1
            
            if key not in edge_index:
                edge_index[key] = []
            edge_index[key].append(len(graph["edges"]) - 1)
            
            logger.debug(f"Added new edge: {source} -> {target} ({edge_type}, conf={new_confidence:.2f})")
            
        else:
            # ==========================================
            # Сценарий 2 и 3: Ребра между этими узлами уже существуют
            # ==========================================
            existing_indices = edge_index[key]
            
            # Ищем ребро с таким же типом
            same_type_idx = None
            for idx in existing_indices:
                if graph["edges"][idx].get("type") == edge_type:
                    same_type_idx = idx
                    break
                    
            if same_type_idx is not None:
                # Сценарий 2: Дубликат (same source, target, type)
                existing_edge = graph["edges"][same_type_idx]
                existing_attrs = existing_edge.get("attributes", {})
                old_confidence = existing_attrs.get("confidence_score", existing_edge.get("weight", 0.5))
                try:
                    old_confidence = float(old_confidence)
                except (ValueError, TypeError):
                    old_confidence = 0.5
                    
                if new_confidence > old_confidence:
                    # Обновляем атрибуты на новые, если уверенность выше
                    existing_edge["attributes"] = new_attrs
                    existing_edge["attributes"]["updated_by"] = "refiner_longrange_v2"
                    existing_edge["type"] = edge_type
                    
                    stats["updated"] += 1
                    stats["types_updated"][edge_type] = stats["types_updated"].get(edge_type, 0) + 1
                    logger.debug(
                        f"Updated edge attributes: {source} -> {target} ({edge_type}), "
                        f"old_conf={old_confidence:.2f}, new_conf={new_confidence:.2f}"
                    )
                else:
                    logger.debug(
                        f"Kept existing edge: {source} -> {target} ({edge_type}), "
                        f"existing_conf={old_confidence:.2f} >= new_conf={new_confidence:.2f}"
                    )
            else:
                # Сценарий 3: Замена типа (same source, target, different type)
                # Находим ребро с максимальной уверенностью среди существующих
                max_confidence = -1.0
                for idx in existing_indices:
                    e_attrs = graph["edges"][idx].get("attributes", {})
                    e_conf = e_attrs.get("confidence_score", graph["edges"][idx].get("weight", 0.5))
                    try:
                        e_conf = float(e_conf)
                    except (ValueError, TypeError):
                        e_conf = 0.5
                    if e_conf > max_confidence:
                        max_confidence = e_conf
                        
                if new_confidence >= max_confidence:
                    # Удаляем все старые ребра между этими узлами
                    removed_edges = []
                    for idx in sorted(existing_indices, reverse=True):
                        removed_edges.append(graph["edges"].pop(idx))
                        
                    # Добавляем новое ребро
                    final_edge = {
                        "source": source,
                        "target": target,
                        "type": edge_type,
                        "attributes": new_attrs
                    }
                    final_edge["attributes"]["replaced_by"] = "refiner_longrange_v2"
                    graph["edges"].append(final_edge)
                    
                    stats["replaced"] += 1
                    for old_edge in removed_edges:
                        old_type = old_edge.get("type", "UNKNOWN")
                        replacement_key = f"{old_type}->{edge_type}"
                        stats["types_replaced"][replacement_key] = stats["types_replaced"].get(replacement_key, 0) + 1
                        
                    logger.debug(
                        f"Replaced edge type: {source} -> {target}, "
                        f"new type={edge_type}, conf={new_confidence:.2f}"
                    )
                    
                    # КРИТИЧНО: Перестраиваем индекс, так как индексы в списке сдвинулись после pop()
                    edge_index = {}
                    for i, edge in enumerate(graph["edges"]):
                        k = (edge.get("source"), edge.get("target"))
                        if k not in edge_index:
                            edge_index[k] = []
                        edge_index[k].append(i)
                else:
                    logger.debug(
                        f"Kept existing edges: {source} -> {target}, "
                        f"max existing conf={max_confidence:.2f} > new_conf={new_confidence:.2f}"
                    )

    # ==========================================
    # Финальная очистка: глобальный запрет самоссылок
    # ==========================================
    # В новой онтологии MaterialsHypothesisGraph самоссылки запрещены для ВСЕХ типов связей
    edges_before = len(graph["edges"])
    graph["edges"] = [
        edge for edge in graph["edges"] 
        if edge.get("source") != edge.get("target")
    ]
    self_loops_removed = edges_before - len(graph["edges"])
    if self_loops_removed > 0:
        stats["self_loops_removed"] = self_loops_removed
        logger.info(f"Removed {self_loops_removed} self-loops globally")

    logger.info(
        f"Graph update complete: added={stats['added']}, "
        f"updated={stats['updated']}, replaced={stats['replaced']}, "
        f"self-loops removed={stats['self_loops_removed']}"
    )
    
    return stats


def add_refiner_meta(graph, config, stats_forward, stats_backward, api_usage_forward, api_usage_backward, timing_forward, timing_backward):
    """Добавляет метаданные refiner со статистикой двух проходов."""
    if "_meta" not in graph:
        graph["_meta"] = {}
    
    total_stats = {
        "added": stats_forward["added"] + (stats_backward["added"] if stats_backward else 0),
        "updated": stats_forward["updated"] + (stats_backward["updated"] if stats_backward else 0),
        "replaced": stats_forward["replaced"] + (stats_backward["replaced"] if stats_backward else 0),
        "types_added": {}, "types_updated": {}, "types_replaced": {}
    }
    
    graph["_meta"]["refiner_longrange"] = {
        "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "model": config["model"],
            "sim_threshold": config["sim_threshold"],
            "max_pairs_per_node": config["max_pairs_per_node"],
            "enable_backward_pass": config.get("enable_backward_pass", True),
        },
        "stats": {
            "forward_pass": stats_forward,
            "backward_pass": stats_backward,
            "total": total_stats
        }
    }


def main():
    try:
        config = load_config()
        refiner_config = config["refiner"]
        
        if not refiner_config.get("run", True):
            print("Refiner longrange is disabled (run=false), copying file without changes")
            input_path = Path("data/out/LearningChunkGraph_dedup.json")
            output_path = Path("data/out/LearningChunkGraph_longrange.json")
            if not input_path.exists():
                return EXIT_INPUT_ERROR
            shutil.copy2(input_path, output_path)
            return EXIT_SUCCESS
            
        logger = setup_json_logging(refiner_config)
        validate_refiner_longrange_config(refiner_config)
        
        input_path = Path("data/out/LearningChunkGraph_dedup.json")
        output_path = Path("data/out/LearningChunkGraph_longrange.json")
        
        graph = load_and_validate_graph(input_path)
        target_nodes = extract_target_nodes(graph)
        
        # Если в графе нет Chunk/Assessment узлов, refiner корректно завершается
        if not target_nodes:
            logger.warning("No Chunk/Assessment nodes found, saving graph without changes")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(graph, f, ensure_ascii=False, indent=2)
            return EXIT_SUCCESS
            
        edges_index = build_edges_index(graph)
        embeddings_dict = get_node_embeddings(target_nodes, refiner_config, logger)
        faiss_index, node_ids_list = build_similarity_index(embeddings_dict, target_nodes, refiner_config, logger)
        
        # Forward pass
        candidate_pairs_forward = generate_candidate_pairs(target_nodes, embeddings_dict, faiss_index, node_ids_list, edges_index, refiner_config, logger, pass_direction="forward")
        if candidate_pairs_forward:
            new_edges_forward, api_usage_forward = analyze_candidate_pairs(candidate_pairs_forward, graph, refiner_config, logger, pass_direction="forward")
            stats_forward = update_graph_with_new_edges(graph, new_edges_forward, logger)
        else:
            new_edges_forward, api_usage_forward = [], {"requests":0, "input_tokens":0, "output_tokens":0, "total_tokens":0}
            stats_forward = {"added": 0, "updated": 0, "replaced": 0, "self_loops_removed": 0, "types_added": {}, "types_updated": {}, "types_replaced": {}}
        
        # Backward pass
        stats_backward = None
        api_usage_backward = None
        if refiner_config.get("enable_backward_pass", True):
            edges_index = build_edges_index(graph)
            candidate_pairs_backward = generate_candidate_pairs(target_nodes, embeddings_dict, faiss_index, node_ids_list, edges_index, refiner_config, logger, pass_direction="backward")
            if candidate_pairs_backward:
                new_edges_backward, api_usage_backward = analyze_candidate_pairs(candidate_pairs_backward, graph, refiner_config, logger, pass_direction="backward")
                stats_backward = update_graph_with_new_edges(graph, new_edges_backward, logger)
            else:
                stats_backward = {"added": 0, "updated": 0, "replaced": 0, "self_loops_removed": 0, "types_added": {}, "types_updated": {}, "types_replaced": {}}
            
        add_refiner_meta(graph, refiner_config, stats_forward, stats_backward, api_usage_forward, api_usage_backward, 0, 0)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)
        return EXIT_SUCCESS
        
    except KeyboardInterrupt:
        return EXIT_RUNTIME_ERROR
    except Exception as e:
        print(f"ERROR: Unexpected error: {e}")
        return EXIT_RUNTIME_ERROR


if __name__ == "__main__":
    sys.exit(main())