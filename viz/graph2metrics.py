#!/usr/bin/env python3
"""
graph2metrics.py - вычисление доменно-специфичных метрик для графа знаний по материаловедению.
Обогащает LearningChunkGraph метриками NetworkX, релевантными для онтологии материалов.
Сохраняет существующие метаданные (например, от graph_fix.py) при перезаписи.
"""
import argparse
import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path

import networkx as nx

try:
    import community as community_louvain
    LOUVAIN_AVAILABLE = True
except ImportError:
    LOUVAIN_AVAILABLE = False

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config import ConfigValidationError, load_config
from src.utils.console_encoding import setup_console_encoding
from src.utils.exit_codes import (
    EXIT_CONFIG_ERROR, EXIT_INPUT_ERROR, EXIT_IO_ERROR,
    EXIT_RUNTIME_ERROR, EXIT_SUCCESS, log_exit,
)
from src.utils.validation import (
    GraphInvariantError, ValidationError,
    validate_graph_invariants, validate_json,
)

# Онтологические типы рёбер для материаловедения
ONTOLOGICAL_EDGE_TYPES = {
    "SYNTHESIZED_BY", "CHARACTERIZED_BY", "IMPROVES", "DEGRADES",
    "CAUSES", "REQUIRES_CONDITION", "HAS_FAILURE_MODE", "MITIGATES",
    "APPLIED_IN", "SUBCLASS_OF", "MENTIONS",
}

ONTOLOGICAL_NODE_TYPES = {
    "Material", "Property", "SynthesisMethod", "CharacterizationMethod",
    "FailureMode", "Mechanism", "Condition", "Application", "Source"
}

def setup_logging(log_file, test_mode=False):
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
    if test_mode:
        logger.info("[TEST MODE] Logging initialized")
    return logger

def load_input_data(input_dir, logger, test_mode=False):
    prefix = "[TEST MODE] " if test_mode else ""
    if test_mode:
        graph_file = input_dir / "tiny_graph.json"
        concepts_file = input_dir / "tiny_concepts.json"
    else:
        graph_file = input_dir / "LearningChunkGraph.json"
        concepts_file = input_dir / "ConceptDictionary.json"

    if not graph_file.exists():
        raise FileNotFoundError(f"Graph file not found: {graph_file}")
    if not concepts_file.exists():
        raise FileNotFoundError(f"Concepts file not found: {concepts_file}")

    logger.info(f"{prefix}Loading input files from {input_dir}")
    with open(graph_file, encoding="utf-8") as f:
        graph_data = json.load(f)
    with open(concepts_file, encoding="utf-8") as f:
        concepts_data = json.load(f)

    try:
        validate_json(graph_data, "LearningChunkGraphNORNIKEL")
        validate_graph_invariants(graph_data)
    except (ValidationError, GraphInvariantError) as e:
        logger.warning(f"Schema validation warning (continuing): {e}")
    try:
        validate_json(concepts_data, "ConceptDictionary")
    except (ValidationError, GraphInvariantError) as e:
        logger.warning(f"Concepts schema warning (continuing): {e}")

    return graph_data, concepts_data

def safe_metric_value(value):
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return 0.0
    return float(value)

def sanitize_graph_weights(G, eps=1e-9):
    self_loops = list(nx.selfloop_edges(G))
    if self_loops:
        G.remove_edges_from(self_loops)
    for u, v, d in G.edges(data=True):
        weight = d.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or weight <= 0:
            weight = eps
        d["weight"] = float(weight)
        if "inverse_weight" not in d:
            d["inverse_weight"] = min(1.0 / max(weight, eps), 1e9)

def convert_to_networkx(graph_data, logger, test_mode=False):
    prefix = "[TEST MODE] " if test_mode else ""
    logger.info(f"{prefix}Converting to NetworkX DiGraph")
    G = nx.DiGraph()
    for node in graph_data["nodes"]:
        G.add_node(node["id"], **node)
    for edge in graph_data["edges"]:
        G.add_edge(
            edge["source"], edge["target"],
            type=edge.get("type"),
            weight=edge.get("weight", 1.0),
            conditions=edge.get("conditions"),
        )
    num_nodes = G.number_of_nodes()
    num_edges = G.number_of_edges()
    num_components = nx.number_weakly_connected_components(G)
    logger.info(f"{prefix}Graph: {num_nodes} nodes, {num_edges} edges, {num_components} components")
    return G

# ============================================================================
# Доменно-специфичные метрики для материаловедения
# ============================================================================

def compute_ontological_pagerank(G, config, logger):
    """
    PageRank только на онтологических рёбрах.
    Показывает наиболее "влиятельные" концепты в домене (материалы, методы, свойства).
    Игнорирует образовательные рёбра (PREREQUISITE, ELABORATES), которых здесь нет.
    """
    if logger:
        logger.info("Computing ontological PageRank")
    
    onto_edges = [
        (u, v, d) for u, v, d in G.edges(data=True) 
        if d.get("type") in ONTOLOGICAL_EDGE_TYPES
    ]
    
    E = nx.DiGraph()
    E.add_nodes_from(G.nodes())
    E.add_weighted_edges_from([(u, v, d.get("weight", 1.0)) for u, v, d in onto_edges])
    
    damping = config.get("graph2metrics", {}).get("pagerank_damping", 0.85)
    if E.number_of_edges() > 0:
        try:
            pr = nx.pagerank(E, alpha=damping, weight="weight")
        except nx.PowerIterationFailedConvergence:
            pr = nx.pagerank(E, alpha=damping, weight="weight", max_iter=200, tol=1e-3)
    else:
        n = E.number_of_nodes()
        pr = {node: 1.0 / n for node in E.nodes()} if n > 0 else {}
    
    return pr

def compute_material_metrics(G, logger):
    """
    Вычисляет специфичные для материаловедения метрики на основе типов рёбер:
    - synthesis_diversity: количество методов синтеза для материала
    - property_coverage: количество изученных свойств материала
    - application_breadth: широта применения материала
    - characterization_richness: количество методов характеризации
    - failure_proximity: близость к узлам FailureMode (чем больше, тем выше риск)
    """
    if logger:
        logger.info("Computing materials science specific metrics")
    
    metrics = {
        "synthesis_diversity": {},
        "property_coverage": {},
        "application_breadth": {},
        "characterization_richness": {},
        "failure_proximity": {},
    }
    
    # Инициализация счётчиков
    for node in G.nodes():
        metrics["synthesis_diversity"][node] = 0
        metrics["property_coverage"][node] = 0
        metrics["application_breadth"][node] = 0
        metrics["characterization_richness"][node] = 0
        metrics["failure_proximity"][node] = 0.0
    
    # Подсчёт рёбер
    for u, v, d in G.edges(data=True):
        edge_type = d.get("type")
        if edge_type == "SYNTHESIZED_BY":
            # u - SynthesisMethod, v - Material
            metrics["synthesis_diversity"][v] = metrics["synthesis_diversity"].get(v, 0) + 1
        elif edge_type in ["IMPROVES", "DEGRADES", "CHARACTERIZED_BY"]:
            # u - Material/Method, v - Property
            metrics["property_coverage"][u] = metrics["property_coverage"].get(u, 0) + 1
        elif edge_type == "APPLIED_IN":
            # u - Material, v - Application
            metrics["application_breadth"][u] = metrics["application_breadth"].get(u, 0) + 1
        elif edge_type == "HAS_FAILURE_MODE":
            # u - Material, v - FailureMode
            metrics["failure_proximity"][u] = metrics["failure_proximity"].get(u, 0.0) + 1.0
    
    # Уточнение failure_proximity через BFS (риск распространяется по графу)
    failure_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "FailureMode"]
    if failure_nodes:
        G_rev = G.reverse(copy=True)
        for fn in failure_nodes:
            try:
                lengths = nx.single_source_shortest_path_length(G_rev, fn, cutoff=5)
                for target, dist in lengths.items():
                    if target != fn:
                        risk_score = 1.0 / (dist + 1)
                        current = metrics["failure_proximity"].get(target, 0.0)
                        metrics["failure_proximity"][target] = max(current, risk_score)
            except nx.NetworkXError:
                pass
    
    return metrics

def compute_louvain_clustering(G, config, logger):
    if not LOUVAIN_AVAILABLE:
        if logger:
            logger.warning("python-louvain not installed, all nodes in cluster 0")
        return {n: 0 for n in G.nodes()}
    if G.number_of_nodes() == 0:
        return {}
    if G.number_of_nodes() == 1:
        return {list(G.nodes())[0]: 0}
    
    UG = nx.Graph()
    for u, v, d in G.edges(data=True):
        weight = float(d.get("weight", 1.0))
        if UG.has_edge(u, v):
            UG[u][v]["weight"] += weight
        else:
            UG.add_edge(u, v, weight=weight)
    for node in G.nodes():
        if node not in UG:
            UG.add_node(node)
    
    resolution = config.get("louvain_resolution", 1.0)
    random_state = config.get("louvain_random_state", 42)
    if logger:
        logger.info(f"Running Louvain (resolution={resolution}, seed={random_state})")
    
    try:
        partition = community_louvain.best_partition(
            UG, weight="weight", resolution=resolution, random_state=random_state
        )
    except Exception as e:
        if logger:
            logger.error(f"Louvain clustering failed: {e}")
        return {n: 0 for n in G.nodes()}
    
    clusters = {}
    for node, cluster_id in partition.items():
        if cluster_id not in clusters:
            clusters[cluster_id] = []
        clusters[cluster_id].append(node)
    
    sorted_clusters = sorted(clusters.items(), key=lambda x: min(x[1]))
    result = {}
    for new_id, (old_id, nodes) in enumerate(sorted_clusters):
        for node in nodes:
            result[node] = new_id
    
    if logger:
        logger.info(f"Found {len(sorted_clusters)} clusters")
    return result

def compute_bridge_scores(G, cluster_map, betweenness_centrality, config):
    w_b = config.get("bridge_weight_betweenness", 0.7)
    bridge_scores = {}
    for node in G.nodes():
        betweenness = betweenness_centrality.get(node, 0.0)
        neighbors = set(G.predecessors(node)) | set(G.successors(node))
        if neighbors and cluster_map:
            node_cluster = cluster_map.get(node)
            inter_count = sum(1 for neighbor in neighbors if cluster_map.get(neighbor) != node_cluster)
            inter_ratio = inter_count / len(neighbors)
        else:
            inter_ratio = 0.0
        bridge_scores[node] = w_b * float(betweenness) + (1.0 - w_b) * float(inter_ratio)
    return bridge_scores

def mark_inter_cluster_edges(G, cluster_map):
    if not cluster_map:
        for u, v, d in G.edges(data=True):
            d["is_inter_cluster_edge"] = False
        return
    for u, v, d in G.edges(data=True):
        source_cluster = cluster_map.get(u)
        target_cluster = cluster_map.get(v)
        if source_cluster is not None and target_cluster is not None:
            is_inter = source_cluster != target_cluster
            d["is_inter_cluster_edge"] = bool(is_inter)
            if is_inter:
                d["source_cluster_id"] = source_cluster
                d["target_cluster_id"] = target_cluster
        else:
            d["is_inter_cluster_edge"] = False

def compute_all_metrics(G, graph_data, config, logger):
    if logger:
        logger.info("Computing all domain-specific graph metrics")
    
    sanitize_graph_weights(G)
    for edge in graph_data.get("edges", []):
        source, target = edge["source"], edge["target"]
        if G.has_edge(source, target):
            edge["inverse_weight"] = G[source][target]["inverse_weight"]
    
    # Базовые метрики
    in_degrees = dict(G.in_degree())
    out_degrees = dict(G.out_degree())
    degree_centrality = nx.degree_centrality(G)
    
    damping = config.get("graph2metrics", {}).get("pagerank_damping", 0.85)
    if G.number_of_edges() > 0:
        try:
            pagerank = nx.pagerank(G, alpha=damping, weight="weight")
        except nx.PowerIterationFailedConvergence:
            pagerank = nx.pagerank(G, alpha=damping, weight="weight", tol=1e-3)
    else:
        n = G.number_of_nodes()
        pagerank = {node: 1.0 / n for node in G.nodes()} if n > 0 else {}
    
    # Distance metrics
    if G.number_of_nodes() >= 3:
        betweenness = nx.betweenness_centrality(G, weight="inverse_weight", normalized=True)
    else:
        betweenness = {n: 0.0 for n in G.nodes()}
    
    # Доменно-специфичные метрики
    onto_pr = compute_ontological_pagerank(G, config, logger)
    mat_metrics = compute_material_metrics(G, logger)
    
    # Кластеризация
    graph2metrics_config = config.get("graph2metrics", {})
    cluster_map = compute_louvain_clustering(G, graph2metrics_config, logger)
    bridge_scores = compute_bridge_scores(G, cluster_map, betweenness, graph2metrics_config)
    mark_inter_cluster_edges(G, cluster_map)
    
    # Запись метрик в узлы
    for node in graph_data["nodes"]:
        node_id = node["id"]
        node["degree_in"] = in_degrees.get(node_id, 0)
        node["degree_out"] = out_degrees.get(node_id, 0)
        node["degree_centrality"] = safe_metric_value(degree_centrality.get(node_id, 0.0))
        node["pagerank"] = safe_metric_value(pagerank.get(node_id, 0.0))
        node["betweenness_centrality"] = safe_metric_value(betweenness.get(node_id, 0.0))
        
        # Доменно-специфичные метрики материаловедения
        node["ontological_pagerank"] = safe_metric_value(onto_pr.get(node_id, 0.0))
        node["synthesis_diversity"] = mat_metrics["synthesis_diversity"].get(node_id, 0)
        node["property_coverage"] = mat_metrics["property_coverage"].get(node_id, 0)
        node["application_breadth"] = mat_metrics["application_breadth"].get(node_id, 0)
        node["characterization_richness"] = mat_metrics["characterization_richness"].get(node_id, 0)
        node["failure_proximity"] = safe_metric_value(mat_metrics["failure_proximity"].get(node_id, 0.0))
        
        # Кластеризация
        node["cluster_id"] = cluster_map.get(node_id, 0)
        node["bridge_score"] = safe_metric_value(bridge_scores.get(node_id, 0.0))
    
    # Inter-cluster edges
    for edge in graph_data.get("edges", []):
        u, v = edge["source"], edge["target"]
        if G.has_edge(u, v):
            edge_data = G[u][v]
            edge["is_inter_cluster_edge"] = edge_data.get("is_inter_cluster_edge", False)
            if edge["is_inter_cluster_edge"]:
                edge["source_cluster_id"] = edge_data.get("source_cluster_id")
                edge["target_cluster_id"] = edge_data.get("target_cluster_id")
    
    return graph_data

def compute_centrality_metrics(G, graph_data, config, logger, test_mode=False):
    prefix = "[TEST MODE] " if test_mode else ""
    num_nodes = G.number_of_nodes()
    print(f"{prefix}Computing metrics for {num_nodes} nodes...")
    if logger:
        logger.info(f"{prefix}Computing all metrics")
    result = compute_all_metrics(G, graph_data, config, logger)
    print("  ✓ All metrics computed successfully")
    return result

def create_mention_index(graph_data, concepts_data):
    node_types = {node["id"]: node.get("type") for node in graph_data.get("nodes", [])}
    mention_index_sets = {}
    for edge in graph_data.get("edges", []):
        source_id = edge["source"]
        target_id = edge["target"]
        if node_types.get(source_id) == "Concept":
            if source_id not in mention_index_sets:
                mention_index_sets[source_id] = set()
            mention_index_sets[source_id].add(target_id)
        if node_types.get(target_id) == "Concept":
            if target_id not in mention_index_sets:
                mention_index_sets[target_id] = set()
            mention_index_sets[target_id].add(source_id)
    
    mention_index = {}
    for concept_id, node_set in mention_index_sets.items():
        mention_index[concept_id] = {"nodes": list(node_set), "count": len(node_set)}
    
    if "_meta" not in concepts_data:
        concepts_data["_meta"] = {}
    concepts_data["_meta"]["mention_index"] = mention_index
    return concepts_data

def link_nodes_to_concepts(graph_data):
    """Связывает узлы с онтологическими сущностями через ребра."""
    node_types = {node["id"]: node.get("type") for node in graph_data.get("nodes", [])}
    node_concepts_sets = {}
    
    for edge in graph_data.get("edges", []):
        source_id = edge["source"]
        target_id = edge["target"]
        source_type = node_types.get(source_id)
        target_type = node_types.get(target_id)
        
        if target_type in ONTOLOGICAL_NODE_TYPES:
            if source_id not in node_concepts_sets:
                node_concepts_sets[source_id] = set()
            node_concepts_sets[source_id].add(target_id)
        
        if source_type in ONTOLOGICAL_NODE_TYPES:
            if target_id not in node_concepts_sets:
                node_concepts_sets[target_id] = set()
            node_concepts_sets[target_id].add(source_id)
    
    for node in graph_data.get("nodes", []):
        node["concepts"] = list(node_concepts_sets.get(node["id"], set()))
    
    return graph_data

def handle_large_graph(graph_data, max_nodes=1000, save_full_path=None, logger=None):
    current_nodes = len(graph_data.get("nodes", []))
    if current_nodes <= max_nodes:
        return graph_data
    if logger:
        logger.warning(f"Graph has {current_nodes} nodes, filtering to top-{max_nodes} by PageRank")
    if save_full_path:
        save_full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_full_path, "w", encoding="utf-8") as f:
            json.dump(graph_data, f, ensure_ascii=False, indent=2)
        if logger:
            logger.info(f"Full graph saved to {save_full_path}")
    
    nodes = graph_data.get("nodes", [])
    nodes.sort(key=lambda n: n.get("pagerank", 0.0), reverse=True)
    kept_nodes = nodes[:max_nodes]
    kept_node_ids = {n["id"] for n in kept_nodes}
    edges = graph_data.get("edges", [])
    kept_edges = [e for e in edges if e["source"] in kept_node_ids and e["target"] in kept_node_ids]
    
    graph_data["nodes"] = kept_nodes
    graph_data["edges"] = kept_edges
    
    if "_meta" not in graph_data:
        graph_data["_meta"] = {}
    if "graph_metadata" not in graph_data["_meta"]:
        graph_data["_meta"]["graph_metadata"] = {}
    graph_data["_meta"]["graph_metadata"].update({
        "filtered": True,
        "original_nodes": current_nodes,
        "original_edges": len(edges),
        "filtered_nodes": len(kept_nodes),
        "filtered_edges": len(kept_edges),
        "filter_method": "top_pagerank",
        "filter_threshold": max_nodes,
    })
    return graph_data

def save_output_data(output_dir, graph_data, concepts_data, logger, test_mode=False):
    prefix = "[TEST MODE] " if test_mode else ""
    concepts_data = create_mention_index(graph_data, concepts_data)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    graph_output = output_dir / "LearningChunkGraph_wow.json"
    concepts_output = output_dir / "ConceptDictionary_wow.json"
    
    # === КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Сохранение существующих метаданных ===
    # Если _wow.json уже существует (например, после graph_fix.py),
    # загружаем его _meta и мержим с новыми метаданными, чтобы не потерять метки.
    existing_meta = {}
    if graph_output.exists():
        try:
            with open(graph_output, encoding="utf-8") as f:
                existing_data = json.load(f)
            existing_meta = existing_data.get("_meta", {})
            logger.info(f"{prefix}Loaded existing metadata from {graph_output.name}")
        except Exception as e:
            logger.warning(f"{prefix}Could not load existing metadata: {e}")
    
    # Мержим: существующие ключи сохраняются, новые добавляются/обновляются
    new_meta = graph_data.get("_meta", {})
    merged_meta = {**existing_meta, **new_meta}
    graph_data["_meta"] = merged_meta
    
    logger.info(f"{prefix}Saving output files to {output_dir}")
    with open(graph_output, "w", encoding="utf-8") as f:
        json.dump(graph_data, f, ensure_ascii=False, indent=2)
    with open(concepts_output, "w", encoding="utf-8") as f:
        json.dump(concepts_data, f, ensure_ascii=False, indent=2)
    logger.info(f"{prefix}Output files saved successfully")

def main():
    setup_console_encoding()
    parser = argparse.ArgumentParser(
        description="Compute domain-specific metrics for K2-18 materials science knowledge graph"
    )
    parser.add_argument(
        "--test-mode", action="store_true",
        help="Use test data from /viz/data/test/ instead of /viz/data/in/"
    )
    args = parser.parse_args()
    
    viz_dir = Path(__file__).parent
    log_file = viz_dir / "logs" / "graph2metrics.log"
    logger = setup_logging(log_file, args.test_mode)
    
    try:
        mode_str = " (TEST MODE)" if args.test_mode else ""
        logger.info(f"=== START graph2metrics{mode_str} ===")
        
        config_path = viz_dir / "config.toml"
        logger.info(f"Loading configuration from {config_path}")
        config = load_config(str(config_path))
        
        if args.test_mode:
            input_dir = viz_dir / "data" / "test"
            print("[TEST MODE] Using test data")
        else:
            input_dir = viz_dir / "data" / "in"
            print("Using production data")
        
        graph_data, concepts_data = load_input_data(input_dir, logger, args.test_mode)
        G = convert_to_networkx(graph_data, logger, args.test_mode)
        
        if args.test_mode:
            print(f"[TEST MODE] Graph loaded: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        else:
            print(f"Graph loaded: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        
        graph_data = compute_centrality_metrics(G, graph_data, config, logger, args.test_mode)
        graph_data = link_nodes_to_concepts(graph_data)
        
        max_display = config.get("visualization", {}).get("max_display_nodes", 1000)
        if len(graph_data["nodes"]) > max_display:
            output_dir = viz_dir / "data" / "out"
            full_path = output_dir / "LearningChunkGraph_wow_full.json"
            graph_data = handle_large_graph(graph_data, max_display, full_path, logger)
        
        output_dir = viz_dir / "data" / "out"
        save_output_data(output_dir, graph_data, concepts_data, logger, args.test_mode)
        
        success_msg = f"Graph metrics computed successfully{mode_str}"
        print(f"✓ {success_msg}")
        logger.info(f"=== SUCCESS graph2metrics{mode_str} ===")
        log_exit(logger, EXIT_SUCCESS, success_msg)
        return EXIT_SUCCESS
    
    except FileNotFoundError as e:
        error_msg = f"Input file not found: {e}"
        print(f"✗ Error: {error_msg}")
        log_exit(logger, EXIT_INPUT_ERROR, error_msg)
        return EXIT_INPUT_ERROR
    except ConfigValidationError as e:
        error_msg = f"Configuration error: {e}"
        print(f"✗ Error: {error_msg}")
        log_exit(logger, EXIT_CONFIG_ERROR, error_msg)
        return EXIT_CONFIG_ERROR
    except (ValidationError, GraphInvariantError) as e:
        error_msg = f"Validation error: {e}"
        print(f"✗ Error: {error_msg}")
        log_exit(logger, EXIT_INPUT_ERROR, error_msg)
        return EXIT_INPUT_ERROR
    except OSError as e:
        error_msg = f"I/O error: {e}"
        print(f"✗ Error: {error_msg}")
        log_exit(logger, EXIT_IO_ERROR, error_msg)
        return EXIT_IO_ERROR
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        print(f"✗ Error: {error_msg}")
        log_exit(logger, EXIT_RUNTIME_ERROR, error_msg)
        return EXIT_RUNTIME_ERROR

if __name__ == "__main__":
    sys.exit(main())