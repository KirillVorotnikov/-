"""
Module for JSON Schema validation and knowledge graph invariants.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Set

import jsonschema
from jsonschema import ValidationError
from src.utils.ontology_config import ONTOLOGY_CONSTRAINTS


# Допустимые классы сущностей
VALID_NODE_TYPES = {
    "Material", "Property", "SynthesisMethod", "CharacterizationMethod",
    "FailureMode", "Mechanism", "Condition", "Application", "Source"
}

# Допустимые типы отношений
VALID_EDGE_TYPES = {
    "IMPROVES", "DEGRADES", "CAUSES", "MITIGATES", "REQUIRES_CONDITION",
    "SYNTHESIZED_BY", "CHARACTERIZED_BY", "HAS_FAILURE_MODE", "APPLIED_IN",
    "SUPPORTED_BY", "SUBCLASS_OF"
}

# Матрица онтологических ограничений (Domain/Range Constraints)


__all__ = [
    "ValidationError",
    "GraphInvariantError",
    "validate_json",
    "validate_graph_invariants",
    "validate_graph_invariants_intermediate",  # NEW
    "validate_concept_dictionary_invariants",
]


class ValidationError(Exception):
    """Data validation error."""

    pass


class GraphInvariantError(ValidationError):
    """Graph invariant error."""

    pass


# Cache for loaded schemas
_SCHEMA_CACHE: Dict[str, Dict] = {}


def _load_schema(schema_name: str) -> Dict[str, Any]:
    """
    Loads JSON Schema from file.

    Args:
        schema_name: Schema name without extension (e.g., 'ConceptDictionary')

    Returns:
        Dictionary with JSON Schema

    Raises:
        FileNotFoundError: If schema file is not found
        ValidationError: If schema is invalid
    """
    if schema_name in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[schema_name]

    # Path to schemas relative to current file
    schema_path = (
        Path(__file__).parent.parent / "schemas" / f"{schema_name}.schema.json"
    )

    if not schema_path.exists():
        raise FileNotFoundError(f"JSON Schema not found: {schema_path}")

    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        # Check that the schema itself is valid
        jsonschema.Draft202012Validator.check_schema(schema)

        _SCHEMA_CACHE[schema_name] = schema
        return schema

    except json.JSONDecodeError as e:
        raise ValidationError(f"Invalid JSON in schema {schema_name}: {e}")
    except jsonschema.SchemaError as e:
        raise ValidationError(f"Invalid JSON Schema {schema_name}: {e}")


def validate_json(data: Dict[str, Any], schema_name: str) -> None:
    """
    Validates data against JSON Schema.

    Args:
        data: Data to validate
        schema_name: Schema name without extension

    Raises:
        ValidationError: If data does not match the schema
    """
    schema = _load_schema(schema_name)

    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as e:
        # Format a clear error message
        error_path = (
            " -> ".join(str(p) for p in e.absolute_path)
            if e.absolute_path
            else "root"
        )
        raise ValidationError(
            f"Schema validation error '{schema_name}' in field '{error_path}': {e.message}"
        )


import logging
from typing import Any, Dict, List, Set
from src.utils.ontology_config import ONTOLOGY_CONSTRAINTS, VALID_EDGE_TYPES, VALID_NODE_TYPES

logger = logging.getLogger(__name__)

def validate_graph_invariants(graph_data: Dict[str, Any]) -> None:
    """Строгая финальная валидация графа."""
    validate_json(graph_data, "LearningChunkGraphNORNIKEL") # Убедитесь, что схема называется именно так
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    node_registry: Dict[str, str] = {}
    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        if not node_id or not node_type:
            raise GraphInvariantError("Found node without ID or Type")
        if node_type not in VALID_NODE_TYPES and node_type not in ["Chunk", "Assessment"]:
            raise GraphInvariantError(f"Invalid node type '{node_type}' for node {node_id}")
        if node_id in node_registry:
            raise GraphInvariantError(f"Duplicate node ID: {node_id}")
        node_registry[node_id] = node_type

    edge_keys: Set[tuple] = set()
    for i, edge in enumerate(edges):
        source = edge.get("source")
        target = edge.get("target")
        edge_type = edge.get("type")

        if source not in node_registry:
            raise GraphInvariantError(f"Edge {i}: source '{source}' does not exist")
        if target not in node_registry:
            raise GraphInvariantError(f"Edge {i}: target '{target}' does not exist")
        if edge_type not in VALID_EDGE_TYPES and edge_type != "MENTIONS":
            raise GraphInvariantError(f"Edge {i}: invalid edge type '{edge_type}'")
        if source == target:
            raise GraphInvariantError(f"Edge {i}: Self-loop forbidden for type '{edge_type}'")

        source_type = node_registry[source]
        target_type = node_registry[target]
        constraints = ONTOLOGY_CONSTRAINTS.get(edge_type)
        if constraints:
            if source_type not in constraints["domain"] or target_type not in constraints["range"]:
                raise GraphInvariantError(f"Edge {i}: Domain/Range violation for '{edge_type}'")
            if edge_type == "SUBCLASS_OF" and source_type != target_type:
                raise GraphInvariantError(f"Edge {i}: SUBCLASS_OF requires matching types")

        edge_key = (source, target, edge_type)
        if edge_key in edge_keys:
            raise GraphInvariantError(f"Edge {i}: duplicate edge")
        edge_keys.add(edge_key)

def validate_graph_invariants_intermediate(graph_data: Dict[str, Any]) -> bool:
    """Мягкая промежуточная валидация (in-place фильтрация)."""
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    valid_nodes, valid_edges = [], []
    node_registry: Dict[str, str] = {}

    for node in nodes:
        node_id, node_type = node.get("id"), node.get("type")
        if not node_id or not node_type: continue
        if node_id in node_registry: continue
        node_registry[node_id] = node_type
        valid_nodes.append(node)

    edge_keys: Set[tuple] = set()
    for edge in edges:
        source, target, edge_type = edge.get("source"), edge.get("target"), edge.get("type")
        if source not in node_registry or target not in node_registry: continue
        if source == target: continue
        
        source_type, target_type = node_registry[source], node_registry[target]
        constraints = ONTOLOGY_CONSTRAINTS.get(edge_type)
        if constraints:
            if source_type not in constraints["domain"] or target_type not in constraints["range"]: continue
            if edge_type == "SUBCLASS_OF" and source_type != target_type: continue
            
        edge_key = (source, target, edge_type)
        if edge_key in edge_keys: continue
        edge_keys.add(edge_key)
        valid_edges.append(edge)

    graph_data["nodes"] = valid_nodes
    graph_data["edges"] = valid_edges
    return True


def validate_concept_dictionary_invariants(concept_data: Dict[str, Any]) -> None:
    """
    Checks concept dictionary invariants.

    Args:
        concept_data: Dictionary data in ConceptDictionary format

    Raises:
        ValidationError: If invariants are violated
    """
    # First validate against schema
    validate_json(concept_data, "ConceptDictionary")

    concepts = concept_data.get("concepts", [])

    # Check concept_id uniqueness
    concept_ids: Set[str] = set()

    for i, concept in enumerate(concepts):
        concept_id = concept.get("concept_id")

        if concept_id in concept_ids:
            raise ValidationError(
                f"Concept {i}: duplicate concept_id '{concept_id}'"
            )

        concept_ids.add(concept_id)

        # Check terms
        term = concept.get("term", {})
        primary = term.get("primary")
        aliases = term.get("aliases", [])

        if primary:
            # if primary in primary_terms:
            #   raise ValidationError(f"Concept {i}: duplicate primary term '{primary}'")
            # primary_terms.add(primary.lower())

            # Check that primary does not repeat in aliases
            if primary.lower() in [alias.lower() for alias in aliases]:
                raise ValidationError(
                    f"Concept {i}: primary term '{primary}' duplicated in aliases"
                )

        # Check aliases for duplicates WITHIN the concept
        alias_set = set()
        for alias in aliases:
            alias_lower = alias.lower()
            if alias_lower in alias_set:
                raise ValidationError(f"Concept {i}: duplicate alias '{alias}'")

            alias_set.add(alias_lower)
            # Removed all_aliases check - aliases can repeat between concepts
