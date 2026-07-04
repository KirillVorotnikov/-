"""
Module for loading and validating iText2KG configuration from TOML file.
Supports OpenRouter API keys and local models only (no OpenAI).
"""
import logging
import os
import sys
from pathlib import Path

# TOML support for different Python versions
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        raise ImportError(
            "tomli library is required for Python < 3.11. Install it with: pip install tomli>=2.0.0"
        )


logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Exception for configuration validation errors."""
    pass


def _inject_env_api_keys(config):
    """
    Injects API keys from environment variables.
    Priority:
    1. Environment variable (OPENROUTER_API_KEY)
    2. Value from config.toml (if not placeholder)
    3. Validation error
    """
    # OpenRouter API key (replaces OpenAI key)
    env_api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")

    # Inject into LLM sections
    for section in ["itext2kg_concepts", "itext2kg_graph", "refiner"]:
        if section in config:
            current_key = config[section].get("api_key", "")
            if env_api_key and (not current_key or current_key.startswith("sk-...") or current_key.startswith("sk-or-...")):
                config[section]["api_key"] = env_api_key


def _find_config_path(config_path):
    """
    Resolves config path with fallback search:
    1. Explicit path (if provided)
    2. Root of project (config.toml)
    3. src/config.toml
    """
    if config_path is not None:
        return Path(config_path)

    # Try root of project first
    root_path = Path.cwd() / "config.toml"
    if root_path.exists():
        return root_path

    # Try src/config.toml
    src_path = Path(__file__).parent.parent / "config.toml"
    if src_path.exists():
        return src_path

    # Fallback to root (will raise FileNotFoundError)
    return root_path


def load_config(config_path=None):
    """
    Loads and validates configuration from TOML file.

    Args:
        config_path: Path to configuration file.
                    If None, searches in project root and src/.

    Returns:
        Dictionary with validated configuration

    Raises:
        ConfigValidationError: On validation errors
        FileNotFoundError: If configuration file not found
    """
    config_path = _find_config_path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    # Load TOML file
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except Exception as e:
        raise ConfigValidationError(f"Failed to parse TOML file: {e}")

    # Check if this is a viz config (has viz-specific sections)
    is_viz_config = "graph2metrics" in config or "visualization" in config

    # Only inject API keys and validate main sections for non-viz configs
    if not is_viz_config:
        _inject_env_api_keys(config)

        try:
            _validate_config(config)
        except Exception as e:
            raise ConfigValidationError(f"Configuration validation failed: {e}")

    # Optional consistency check (warning, not error)
    for section in ["itext2kg_concepts", "itext2kg_graph", "refiner"]:
        if section not in config:
            continue

        is_reasoning = config[section].get("is_reasoning", False)
        has_temperature = config[section].get("temperature") is not None
        has_reasoning_effort = config[section].get("reasoning_effort") is not None

        if is_reasoning and has_temperature:
            logger.warning(
                f"[{section}] Reasoning model with temperature parameter - "
                f"might be ignored by API"
            )
        if not is_reasoning and has_reasoning_effort:
            logger.warning(
                f"[{section}] Non-reasoning model with reasoning_effort - "
                f"will be ignored by API"
            )

    return config


def _validate_config(config):
    """Validates the full configuration structure."""
    required_sections = ["slicer", "itext2kg_concepts", "itext2kg_graph", "dedup", "refiner"]
    for section in required_sections:
        if section not in config:
            raise ConfigValidationError(f"Missing required section: [{section}]")

    _validate_slicer_section(config["slicer"])
    _validate_itext2kg_concepts_section(config["itext2kg_concepts"])
    _validate_itext2kg_graph_section(config["itext2kg_graph"])
    _validate_dedup_section(config["dedup"])
    _validate_refiner_section(config["refiner"])


def _validate_slicer_section(section):
    """Validates the [slicer] section."""
    required_fields = {
        "max_tokens": int,
        "soft_boundary": bool,
        "soft_boundary_max_shift": int,
        "allowed_extensions": list,
    }
    _validate_required_fields(section, required_fields, "slicer")

    # Check value ranges
    if section["max_tokens"] <= 0:
        raise ConfigValidationError("slicer.max_tokens must be positive")

    if section["soft_boundary_max_shift"] < 0:
        raise ConfigValidationError("slicer.soft_boundary_max_shift must be non-negative")

    # Check tokenizer_path (accepts any non-empty string: local path or HF model name)
    tokenizer_path = section.get("tokenizer_path") or section.get("tokenizer")
    if not tokenizer_path or not isinstance(tokenizer_path, str):
        raise ConfigValidationError(
            "slicer.tokenizer_path must be a non-empty string "
            "(local model path or HuggingFace model name)"
        )

    # Check allowed_extensions
    if not section["allowed_extensions"]:
        raise ConfigValidationError("slicer.allowed_extensions cannot be empty")


def _validate_llm_section(section, section_name):
    """Shared validation for LLM sections (concepts, graph, refiner)."""
    required_fields = {
        "model": str,
        "tpm_limit": int,
        "max_completion": int,
        "log_level": str,
        "api_key": str,
        "timeout": int,
        "max_retries": int,
    }
    _validate_required_fields(section, required_fields, section_name)

    # Check ranges
    if section["tpm_limit"] <= 0:
        raise ConfigValidationError(f"{section_name}.tpm_limit must be positive")

    if not (1 <= section["max_completion"] <= 100000):
        raise ConfigValidationError(f"{section_name}.max_completion must be between 1 and 100000")

    if section["log_level"] not in ["debug", "info", "warning", "error"]:
        raise ConfigValidationError(
            f"{section_name}.log_level must be one of: debug, info, warning, error"
        )

    # API key check: placeholder or empty → require env variable
    api_key = section["api_key"]
    if not api_key.strip() or api_key.startswith("sk-...") or api_key.startswith("sk-or-..."):
        if not (os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")):
            raise ConfigValidationError(
                f"{section_name}.api_key not configured. Either:\n"
                "1. Set OPENROUTER_API_KEY environment variable\n"
                "2. Provide valid key in config.toml"
            )

    if section["timeout"] <= 0:
        raise ConfigValidationError(f"{section_name}.timeout must be positive")

    if section["max_retries"] < 0:
        raise ConfigValidationError(f"{section_name}.max_retries must be non-negative")

    # Check temperature if specified
    if "temperature" in section:
        temp = section["temperature"]
        if not (0 <= temp <= 2):
            raise ConfigValidationError(f"{section_name}.temperature must be between 0 and 2")

    # Validate optional response_chain_depth
    if "response_chain_depth" in section:
        depth = section["response_chain_depth"]
        if not isinstance(depth, int) or depth < 0:
            raise ConfigValidationError(
                f"{section_name}.response_chain_depth must be a non-negative integer"
            )

    # Validate optional truncation
    if "truncation" in section:
        truncation = section["truncation"]
        if truncation not in ["auto", "disabled"]:
            raise ConfigValidationError(f"{section_name}.truncation must be 'auto' or 'disabled'")


def _validate_itext2kg_concepts_section(section):
    """Validates the [itext2kg_concepts] section."""
    _validate_llm_section(section, "itext2kg_concepts")


def _validate_itext2kg_graph_section(section):
    """Validates the [itext2kg_graph] section."""
    _validate_llm_section(section, "itext2kg_graph")

    # Validate optional auto_mentions_weight (only for graph)
    if "auto_mentions_weight" in section:
        weight = section["auto_mentions_weight"]
        if not isinstance(weight, (int, float)) or not (0.0 <= weight <= 1.0):
            raise ConfigValidationError(
                "itext2kg_graph.auto_mentions_weight must be between 0.0 and 1.0"
            )


def _validate_dedup_section(section):
    """Validates the [dedup] section (local embeddings only)."""
    required_fields = {
        "embedding_model": str,
        "sim_threshold": float,
        "len_ratio_min": float,
        "faiss_M": int,
        "faiss_efC": int,
        "faiss_metric": str,
        "k_neighbors": int,
    }
    _validate_required_fields(section, required_fields, "dedup")

    # Check ranges
    if not (0.0 <= section["sim_threshold"] <= 1.0):
        raise ConfigValidationError("dedup.sim_threshold must be between 0.0 and 1.0")

    if not (0.0 <= section["len_ratio_min"] <= 1.0):
        raise ConfigValidationError("dedup.len_ratio_min must be between 0.0 and 1.0")

    if section["faiss_M"] <= 0:
        raise ConfigValidationError("dedup.faiss_M must be positive")

    if section["faiss_efC"] <= 0:
        raise ConfigValidationError("dedup.faiss_efC must be positive")

    if section["faiss_metric"] not in ["INNER_PRODUCT", "L2"]:
        raise ConfigValidationError("dedup.faiss_metric must be 'INNER_PRODUCT' or 'L2'")

    if section["k_neighbors"] <= 0:
        raise ConfigValidationError("dedup.k_neighbors must be positive")

    # Embeddings are local-only (sentence-transformers), no API key required


def _validate_refiner_section(section):
    """Validates the [refiner] section."""
    # Shared LLM validation
    _validate_llm_section(section, "refiner")

    # Refiner-specific required fields
    refiner_required = {
        "run": bool,
        "embedding_model": str,
        "sim_threshold": float,
        "max_pairs_per_node": int,
    }
    _validate_required_fields(section, refiner_required, "refiner")

    # Check ranges
    if not (0.0 <= section["sim_threshold"] <= 1.0):
        raise ConfigValidationError("refiner.sim_threshold must be between 0.0 and 1.0")

    if section["max_pairs_per_node"] <= 0:
        raise ConfigValidationError("refiner.max_pairs_per_node must be positive")


def _validate_required_fields(section, required_fields, section_name):
    """Checks presence and types of required fields in a section."""
    for field_name, expected_type in required_fields.items():
        if field_name not in section:
            raise ConfigValidationError(f"Missing required field: {section_name}.{field_name}")

        actual_value = section[field_name]
        if not isinstance(actual_value, expected_type):
            raise ConfigValidationError(
                f"Field {section_name}.{field_name} must be {expected_type.__name__}, "
                f"got {type(actual_value).__name__}"
            )