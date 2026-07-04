#!/usr/bin/env python3
"""
iText2KG Graph - incremental knowledge graph construction.
Supports both full rebuild and incremental mode (adding new files).
"""
from dotenv import load_dotenv
load_dotenv()

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config import load_config
from src.utils.llm_providers import LLMClientFactory
from src.utils.ontology_config import ONTOLOGY_CONSTRAINTS
from src.utils.console_encoding import setup_console_encoding
from src.utils.exit_codes import (
    EXIT_CONFIG_ERROR, EXIT_INPUT_ERROR, EXIT_IO_ERROR,
    EXIT_RUNTIME_ERROR, EXIT_SUCCESS,
)

setup_console_encoding()

CONFIG_PATH = Path(__file__).parent / "config.toml"
PROMPTS_DIR = Path(__file__).parent / "prompts"
SCHEMAS_DIR = Path(__file__).parent / "schemas"
STAGING_DIR = Path(__file__).parent.parent / "data" / "staging"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "out"
LOGS_DIR = Path(__file__).parent.parent / "logs"
GRAPH_EXTRACTION_PROMPT_FILE = "itext2kg_graph_extraction.md"
DEFAULT_DIFFICULTY = 3


class ProcessingStats:
    def __init__(self):
        self.total_slices = 0
        self.processed_slices = 0
        self.skipped_slices = 0
        self.failed_slices = 0
        self.total_nodes = 0
        self.total_edges = 0
        self.total_tokens_used = 0
        self.start_time = datetime.now()


class SliceData:
    def __init__(self, id, order, source_file, slug, text, slice_token_start, slice_token_end):
        self.id = id
        self.order = order
        self.source_file = source_file
        self.slug = slug
        self.text = text
        self.slice_token_start = slice_token_start
        self.slice_token_end = slice_token_end


class SliceProcessor:
    def __init__(self, config, incremental=False):
        self.config = config["itext2kg_graph"]
        self.full_config = config
        self.incremental = incremental
        self.llm_client = LLMClientFactory.create_client(self.config)
        self.logger = self._setup_logger()
        self.stats = ProcessingStats()
        
        self.quality_issues = {"duplicate_concepts_removed": 0, "anomalous_duplicates": []}
        self.concept_dict = self._load_concept_dictionary()
        
        # Структуры графа
        self.graph_nodes = []
        self.graph_edges = []
        self.node_ids = {}
        
        # === INCREMENTAL: отслеживание обработанных слайсов ===
        self.processed_slice_ids = set()
        self.processed_source_files = set()
        
        self.previous_response_id = None
        self.api_usage = {
            "total_requests": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        }
        
        # Загружаем существующий граф в incremental режиме
        if self.incremental:
            self._load_existing_graph()
        
        self.extraction_prompt = self._load_extraction_prompt()

    def _load_existing_graph(self):
        """Загружает существующий граф для incremental режима."""
        graph_path = OUTPUT_DIR / "LearningChunkGraph_raw.json"
        
        if not graph_path.exists():
            self.logger.warning("No existing graph found, starting fresh")
            self.incremental = False
            return
        
        try:
            with open(graph_path, encoding="utf-8") as f:
                data = json.load(f)
            
            self.graph_nodes = data.get("nodes", [])
            self.graph_edges = data.get("edges", [])
            
            # Строим индекс узлов
            for idx, node in enumerate(self.graph_nodes):
                self.node_ids[node["id"]] = idx
            
            # Извлекаем список обработанных слайсов из метаданных
            meta = data.get("_meta", {}).get("itext2kg_graph", {})
            self.processed_slice_ids = set(meta.get("processed_slice_ids", []))
            self.processed_source_files = set(meta.get("processed_source_files", []))
            
            self.stats.total_nodes = len(self.graph_nodes)
            self.stats.total_edges = len(self.graph_edges)
            
            self.logger.info(
                f"Loaded existing graph: {len(self.graph_nodes)} nodes, "
                f"{len(self.graph_edges)} edges, "
                f"{len(self.processed_slice_ids)} processed slices"
            )
            print(
                f"Incremental mode: loaded {len(self.graph_nodes)} nodes, "
                f"{len(self.graph_edges)} edges"
            )
            print(f"  Already processed: {len(self.processed_slice_ids)} slices from "
                  f"{len(self.processed_source_files)} file(s)")
        
        except Exception as e:
            self.logger.error(f"Failed to load existing graph: {e}")
            self.logger.warning("Starting with empty graph")
            self.incremental = False

    def _format_tokens(self, tokens):
        if tokens < 1000:
            return str(tokens)
        elif tokens < 1_000_000:
            return f"{tokens / 1000:.2f}k"
        else:
            return f"{tokens / 1_000_000:.2f}M"

    def _setup_logger(self):
        logger = logging.getLogger("itext2kg_graph")
        logger.setLevel(getattr(logging, self.config["log_level"].upper()))
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = LOGS_DIR / f"itext2kg_graph_{timestamp}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(file_handler)
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")
        )
        logger.addHandler(console_handler)
        
        return logger

    def _load_concept_dictionary(self):
        concept_dict_path = OUTPUT_DIR / "ConceptDictionary.json"
        if not concept_dict_path.exists():
            self.logger.error(f"ConceptDictionary not found: {concept_dict_path}")
            sys.exit(EXIT_INPUT_ERROR)
        
        try:
            with open(concept_dict_path, encoding="utf-8") as f:
                concept_dict = json.load(f)
            if "concepts" not in concept_dict:
                self.logger.error("Invalid ConceptDictionary structure")
                sys.exit(EXIT_INPUT_ERROR)
            self.logger.info(f"Loaded {len(concept_dict['concepts'])} concepts")
            print(f"Loaded {len(concept_dict['concepts'])} concepts from ConceptDictionary.json")
            return concept_dict
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse ConceptDictionary.json: {e}")
            sys.exit(EXIT_INPUT_ERROR)

    def _load_extraction_prompt(self):
        prompt_file = PROMPTS_DIR / GRAPH_EXTRACTION_PROMPT_FILE
        if not prompt_file.exists():
            self.logger.error(f"Graph extraction prompt not found: {prompt_file}")
            sys.exit(EXIT_CONFIG_ERROR)
        
        try:
            with open(prompt_file, encoding="utf-8") as f:
                prompt_template = f.read()
                schema_file = SCHEMAS_DIR / "LearningChunkGraphNORNIKEL.schema.json"
            with open(schema_file, encoding="utf-8") as f:
                learning_chunk_schema = f.read()
            prompt = prompt_template.replace("{learning_chunk_graph_schema}", learning_chunk_schema)
            return prompt
        except Exception as e:
            self.logger.error(f"Failed to load extraction prompt: {e}")
            sys.exit(EXIT_CONFIG_ERROR)

    def _load_slice(self, slice_file):
        try:
            with open(slice_file, encoding="utf-8") as f:
                data = json.load(f)
            return SliceData(
                id=data["id"], order=data["order"], source_file=data["source_file"],
                slug=data["slug"], text=data["text"],
                slice_token_start=data["slice_token_start"],
                slice_token_end=data["slice_token_end"],
            )
        except Exception as e:
            self.logger.error(f"Failed to load slice {slice_file}: {e}")
            raise

    def _format_slice_input(self, slice_data):
        input_obj = {
            "ConceptDictionary": self.concept_dict,
            "Slice": {
                "id": slice_data.id, "order": slice_data.order,
                "source_file": slice_data.source_file, "slug": slice_data.slug,
                "text": slice_data.text,
                "slice_token_start": slice_data.slice_token_start,
                "slice_token_end": slice_data.slice_token_end,
            },
        }
        return json.dumps(input_obj, ensure_ascii=False)

    def _process_llm_response(self, response_text, slice_id):
        try:
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                if len(lines) > 1:
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines)
            
            # HTML атрибут cleanup
            attributes = ["href", "src", "target", "action", "name", "frameborder", "width", "height", "align"]
            for attr in attributes:
                cleaned = re.sub(f'{attr}=[\'"]\\\\"([^"]*)\\\\"[\'"]', f'{attr}="\\1"', cleaned)
                cleaned = re.sub(f"{attr}=\"'([^']*)'\"", f'{attr}="\\1"', cleaned)
            
            parsed = json.loads(cleaned)
            
            if "chunk_graph_patch" not in parsed:
                self.logger.error(f"Missing 'chunk_graph_patch' in response for {slice_id}")
                return False, None
            
            patch = parsed["chunk_graph_patch"]
            if "nodes" not in patch or "edges" not in patch:
                return False, None
            if not isinstance(patch["nodes"], list) or not isinstance(patch["edges"], list):
                return False, None
            
            return True, parsed
        
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON decode error for {slice_id}: {e}")
            return False, None
        except Exception as e:
            self.logger.error(f"Unexpected error processing response for {slice_id}: {e}")
            return False, None

    def _process_chunk_nodes(self, new_nodes):
        nodes_to_add = []
        concept_lookup = {c["concept_id"]: c for c in self.concept_dict["concepts"]}
        valid_concept_types = {
            "Material", "Property", "SynthesisMethod", "CharacterizationMethod",
            "FailureMode", "Mechanism", "Condition", "Application", "Source"
        }

        for node in new_nodes:
            node_id = node.get("id", "")
            node_type = node.get("type", "")

            if node_type in ["Chunk", "Assessment"]:
                if node_id in self.node_ids:
                    existing_idx = self.node_ids[node_id]
                    existing_node = self.graph_nodes[existing_idx]
                    if node_type == "Chunk" and len(node.get("text", "")) > len(existing_node.get("text", "")):
                        self.graph_nodes[existing_idx] = node
                else:
                    if node_type == "Chunk" and "difficulty" not in node:
                        node["difficulty"] = DEFAULT_DIFFICULTY
                    nodes_to_add.append(node)
                    self.node_ids[node_id] = len(self.graph_nodes) + len(nodes_to_add) - 1
                    
            elif node_type in valid_concept_types and node_id in concept_lookup:
                if node_id in self.node_ids: continue # Пропуск дубликатов
                concept_from_dict = concept_lookup[node_id]
                concept_node = {
                    "id": node_id,
                    "type": node_type,
                    "name": node.get("name", concept_from_dict["term"]["primary"]),
                    "metadata": node.get("metadata", {})
                }
                nodes_to_add.append(concept_node)
                self.node_ids[node_id] = len(self.graph_nodes) + len(nodes_to_add) - 1
            else:
                self.logger.warning(f"Node {node_id} ({node_type}) not found in ConceptDictionary or invalid type")
                
        return nodes_to_add

    def _validate_edges(self, edges):
        valid_edges = []
        existing_edge_keys = {(e["source"], e["target"], e["type"]) for e in self.graph_edges}
        
        node_type_lookup = {n["id"]: n["type"] for n in self.graph_nodes}
        for c in self.concept_dict["concepts"]:
            node_type_lookup[c["concept_id"]] = c.get("ontology_class", "Concept")

        causal_types = {"IMPROVES", "DEGRADES", "CAUSES", "MITIGATES", "REQUIRES_CONDITION", "HAS_FAILURE_MODE"}

        for edge in edges:
            source, target, edge_type = edge.get("source"), edge.get("target"), edge.get("type")
            if source == target: continue

            attrs = edge.get("attributes", {})
            if not isinstance(attrs, dict): attrs = {}
            
            conf = attrs.get("confidence_score", edge.get("weight", 0.5))
            try: conf = float(conf)
            except: conf = 0.5
            if conf < 0.5: continue # Отбрасываем слабые связи

            if source not in node_type_lookup or target not in node_type_lookup: continue
            
            s_type, t_type = node_type_lookup[source], node_type_lookup[target]
            constraints = ONTOLOGY_CONSTRAINTS.get(edge_type)
            if constraints:
                if s_type not in constraints["domain"] or t_type not in constraints["range"]: continue
                if edge_type == "SUBCLASS_OF" and s_type != t_type: continue
            else:
                continue

            edge_key = (source, target, edge_type)
            if edge_key in existing_edge_keys: continue

            attrs["confidence_score"] = conf
            if "relation_role" not in attrs:
                attrs["relation_role"] = "causal" if edge_type in causal_types else "structural"
                
            valid_edges.append({"source": source, "target": target, "type": edge_type, "attributes": attrs})
            existing_edge_keys.add(edge_key)

        return valid_edges

    def _assign_final_ids(self, patch, slice_data):
        id_mapping = {}
        
        for node in patch.get("nodes", []):
            old_id = node.get("id", "")
            node_type = node.get("type", "")
            if "node_offset" not in node:
                continue
            node_offset = node["node_offset"]
            
            if node_type == "Chunk" and old_id.startswith("chunk_"):
                final_position = slice_data.slice_token_start + node_offset
                new_id = f"{slice_data.slug}:c:{final_position}"
                node["id"] = new_id
                id_mapping[old_id] = new_id
            elif node_type == "Assessment" and old_id.startswith("assessment_"):
                final_position = slice_data.slice_token_start + node_offset
                try:
                    index = old_id.split("_")[-1] if "_" in old_id else "0"
                except Exception:
                    index = "0"
                new_id = f"{slice_data.slug}:q:{final_position}:{index}"
                node["id"] = new_id
                id_mapping[old_id] = new_id
        
        for edge in patch.get("edges", []):
            if edge.get("source") in id_mapping:
                edge["source"] = id_mapping[edge["source"]]
            if edge.get("target") in id_mapping:
                edge["target"] = id_mapping[edge["target"]]

    def _deduplicate_patch_nodes(self, patch, slice_id):
        existing_ids = set(node.get("id", "") for node in self.graph_nodes)
        deduplicated_nodes = []
        concepts_removed = 0
        anomalous_duplicates = []
        
        for node in patch.get("nodes", []):
            node_id = node.get("id", "")
            node_type = node.get("type", "")
            if node_id in existing_ids:
                if node_type not in ["Chunk", "Assessment"]:
                    concepts_removed += 1
                else:
                    anomalous_duplicates.append({"node_id": node_id, "node_type": node_type, "slice_id": slice_id})
            else:
                deduplicated_nodes.append(node)
                existing_ids.add(node_id)
        
        self.quality_issues["duplicate_concepts_removed"] += concepts_removed
        self.quality_issues["anomalous_duplicates"].extend(anomalous_duplicates)
        return {**patch, "nodes": deduplicated_nodes}

    def _add_to_graph(self, patch, slice_data):
        patch = self._deduplicate_patch_nodes(patch, slice_data.id)
        
        new_nodes = patch.get("nodes", [])
        nodes_to_add = self._process_chunk_nodes(new_nodes)
        self.graph_nodes.extend(nodes_to_add)
        self.stats.total_nodes = len(self.graph_nodes)
        
        new_edges = patch.get("edges", [])
        valid_edges = self._validate_edges(new_edges)
        self.graph_edges.extend(valid_edges)
        self.stats.total_edges = len(self.graph_edges)

    def _validate_graph_intermediate(self):
        chunk_assessment_ids = set()
        for node in self.graph_nodes:
            if node.get("type") in ["Chunk", "Assessment"]:
                node_id = node.get("id", "")
                if node_id in chunk_assessment_ids:
                    return False
                chunk_assessment_ids.add(node_id)
        return True

    def _save_bad_response(self, slice_id, original_response, error, repair_response=None):
        bad_response_file = LOGS_DIR / f"{slice_id}_bad.json"
        bad_data = {
            "slice_id": slice_id, "timestamp": datetime.now().isoformat(),
            "original_response": original_response, "error": error,
            "repair_response": repair_response,
        }
        try:
            bad_response_file.write_text(json.dumps(bad_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            self.logger.error(f"Failed to save bad response: {e}")

    def _save_temp_dumps(self, reason):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        graph_path = LOGS_DIR / f"LearningChunkGraph_temp_{reason}_{timestamp}.json"
        graph_data = {"nodes": self.graph_nodes, "edges": self.graph_edges}
        try:
            graph_path.write_text(json.dumps(graph_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            self.logger.error(f"Failed to save temporary graph: {e}")

    def _process_single_slice(self, slice_file):
        try:
            slice_data = self._load_slice(slice_file)
        except Exception as e:
            self.logger.error(f"Failed to load slice {slice_file}: {e}")
            return False
        
        # === INCREMENTAL: пропускаем уже обработанные слайсы ===
        if self.incremental and slice_data.id in self.processed_slice_ids:
            self.stats.skipped_slices += 1
            self.logger.info(f"Skipping already processed slice: {slice_data.id}")
            return True
        
        slice_id = slice_data.id
        slice_order = slice_data.order
        input_data = self._format_slice_input(slice_data)
        max_retries = self.config.get("max_retries", 3)
        last_error_type = None
        start_time = time.time()
        
        for attempt in range(max_retries + 1):
            try:
                if attempt == 0:
                    response_text, response_id, usage = self.llm_client.create_response(
                        instructions=self.extraction_prompt,
                        input_data=input_data,
                        previous_response_id=self.previous_response_id,
                    )
                else:
                    current_time = datetime.now().strftime("%H:%M:%S")
                    print(f"[{current_time}] REPAIR   | 🔧 Attempt {attempt}/{max_retries} after {last_error_type}...")
                    
                    repair_hint = ""
                    if last_error_type == "json":
                        repair_hint = "\nCRITICAL: Return ONLY a valid JSON object. No markdown."
                    elif last_error_type == "timeout":
                        repair_hint = "\nIMPORTANT: Be concise to avoid timeout."
                    
                    response_text, response_id, usage = self.llm_client.repair_response(
                        instructions=self.extraction_prompt + repair_hint,
                        input_data=input_data,
                        previous_response_id=self.previous_response_id,
                    )
                
                self.api_usage["total_requests"] += 1
                self.api_usage["total_input_tokens"] += usage.input_tokens
                self.api_usage["total_output_tokens"] += usage.output_tokens
                self.stats.total_tokens_used += usage.total_tokens
                
                success, parsed = self._process_llm_response(response_text, slice_id)
                if not success:
                    last_error_type = "json"
                    if attempt == max_retries:
                        self._save_bad_response(slice_id, response_text, "JSON validation failed")
                        current_time = datetime.now().strftime("%H:%M:%S")
                        print(f"[{current_time}] FAILED   | ❌ JSON validation failed for {slice_id}")
                        self._save_temp_dumps(f"critical_slice_failure_{slice_id}")
                        return False
                    continue
                
                self.llm_client.confirm_response()
                
                if parsed and "chunk_graph_patch" in parsed:
                    patch = parsed["chunk_graph_patch"]
                    self._assign_final_ids(patch, slice_data)
                    self._add_to_graph(patch, slice_data)
                    
                    if not self._validate_graph_intermediate():
                        self._save_temp_dumps(f"validation_error_{slice_id}")
                        return False
                    
                    # === INCREMENTAL: отмечаем слайс как обработанный ===
                    self.processed_slice_ids.add(slice_id)
                    self.processed_source_files.add(slice_data.source_file)
                    self.previous_response_id = response_id
                    
                    elapsed = int(time.time() - start_time)
                    current_time = datetime.now().strftime("%H:%M:%S")
                    print(
                        f"[{current_time}] SLICE    | ✅ {slice_order:03d}/{self.stats.total_slices} | "
                        f"tokens_used={self._format_tokens(self.stats.total_tokens_used)} | "
                        f"{elapsed}s | nodes={self.stats.total_nodes} | edges={self.stats.total_edges}"
                    )
                    return True
                return False
            
            except TimeoutError as e:
                last_error_type = "timeout"
                if attempt == max_retries:
                    self._save_temp_dumps(f"timeout_failure_{slice_id}")
                    return False
                time.sleep(30 * (attempt + 1))
            
            except Exception as e:
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] FAILED   | ❌ Unexpected error: {type(e).__name__}")
                self._save_temp_dumps(f"unexpected_error_{slice_id}")
                return False
        
        return False

    def run(self):
        slice_files = sorted(STAGING_DIR.glob("*.slice.json"))
        if not slice_files:
            self.logger.error("No slice files found in staging directory")
            return EXIT_INPUT_ERROR
        
        # === INCREMENTAL: фильтруем уже обработанные слайсы ===
        if self.incremental and self.processed_slice_ids:
            original_count = len(slice_files)
            slice_files = [
                sf for sf in slice_files
                if self._get_slice_id(sf) not in self.processed_slice_ids
            ]
            filtered = original_count - len(slice_files)
            if filtered > 0:
                self.logger.info(f"Incremental mode: skipped {filtered} already processed slices")
                self.stats.skipped_slices = filtered
        
        if not slice_files:
            self.logger.info("No new slices to process - graph is up to date")
            print("No new slices to process - graph is up to date")
            # Всё равно сохраняем граф (с обновлёнными метаданными)
            return self._finalize_and_save()
        
        self.stats.total_slices = len(slice_files)
        
        self.total_source_tokens = 0
        self.source_slug = "unknown"
        if slice_files:
            try:
                first_slice_data = json.loads(slice_files[0].read_text(encoding="utf-8"))
                self.source_slug = first_slice_data.get("slug", "unknown")
                last_slice_data = json.loads(slice_files[-1].read_text(encoding="utf-8"))
                self.total_source_tokens = last_slice_data.get("slice_token_end", 0)
            except Exception:
                pass
        
        model = self.config["model"]
        tpm_limit = self.config["tpm_limit"]
        mode = "incremental" if self.incremental else "full"
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(
            f"[{timestamp}] START    | {len(slice_files)} new slices | "
            f"mode={mode} | model={model} | tpm={tpm_limit // 1000}k"
        )
        
        for slice_file in slice_files:
            success = self._process_single_slice(slice_file)
            if success:
                self.stats.processed_slices += 1
            else:
                self.stats.failed_slices += 1
                return EXIT_RUNTIME_ERROR
        
        if self.stats.processed_slices == 0 and self.stats.skipped_slices == 0:
            return EXIT_RUNTIME_ERROR
        
        return self._finalize_and_save()

    def _add_mentions_edges(self, chunk_nodes):
        added_count = 0
        mentions_weight = self.config.get("auto_mentions_weight", 0.35)
        existing_mentions = {(e["source"], e["target"]) for e in self.graph_edges if e["type"] == "MENTIONS"}

        for chunk in chunk_nodes:
            if chunk.get("type") != "Chunk": continue
            chunk_id, chunk_text = chunk["id"], chunk.get("text", "").lower()
            
            for concept in self.concept_dict["concepts"]:
                concept_id = concept["concept_id"]
                if (chunk_id, concept_id) in existing_mentions: continue
                
                found = any(re.search(r"\b" + re.escape(t.lower()) + r"\b", chunk_text) 
                            for t in [concept["term"]["primary"]] + concept["term"].get("aliases", []))
                
                if found:
                    edge = {
                        "source": chunk_id, "target": concept_id, "type": "MENTIONS",
                        "attributes": {
                            "confidence_score": mentions_weight,
                            "relation_role": "navigational",
                            "evidence_quote": "auto_generated",
                            "source_doi": "chunk_metadata"
                        }
                    }
                    self.graph_edges.append(edge)
                    existing_mentions.add((chunk_id, concept_id))
                    added_count += 1
        return added_count

    def _get_slice_id(self, slice_file):
        try:
            with open(slice_file, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("id", "")
        except Exception:
            return ""

    def _finalize_and_save(self):
        output_path = OUTPUT_DIR / "LearningChunkGraph_raw.json"
        
        try:
            graph_stats = {
                "total_nodes": len(self.graph_nodes),
                "chunks": len([n for n in self.graph_nodes if n.get("type") == "Chunk"]),
                "concepts": len([n for n in self.graph_nodes if n.get("type") not in ["Chunk", "Assessment"]]),
                "assessments": len([n for n in self.graph_nodes if n.get("type") == "Assessment"]),
                "total_edges": len(self.graph_edges),
                "edge_types": {},
            }
            for edge in self.graph_edges:
                edge_type = edge.get("type", "UNKNOWN")
                graph_stats["edge_types"][edge_type] = graph_stats["edge_types"].get(edge_type, 0) + 1
            
            config = self.config.copy()
            slicer_config = self.full_config.get("slicer", {})
            concepts_count = len(self.concept_dict.get("concepts", []))
            end_time = datetime.now()
            duration_minutes = (end_time - self.stats.start_time).total_seconds() / 60
            
            metadata = {
                "_meta": {
                    "itext2kg_graph": {
                        "generated_at": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "mode": "incremental" if self.incremental else "full",
                        "config": {
                            "model": config.get("model"),
                            "temperature": config.get("temperature"),
                            "max_output_tokens": config.get("max_completion"),
                            "overlap": slicer_config.get("overlap", 0),
                            "slice_size": slicer_config.get("max_tokens", 5000),
                            "auto_mentions_weight": config.get("auto_mentions_weight", 0.35),
                        },
                        "source": {
                            "total_slices": self.stats.total_slices + self.stats.skipped_slices,
                            "processed_slices": self.stats.processed_slices + self.stats.skipped_slices,
                            "skipped_slices": self.stats.skipped_slices,
                            "total_tokens": self.total_source_tokens,
                            "slug": self.source_slug,
                            "concepts_used": concepts_count,
                        },
                        "api_usage": {
                            "total_requests": self.api_usage["total_requests"],
                            "total_input_tokens": self.api_usage["total_input_tokens"],
                            "total_output_tokens": self.api_usage["total_output_tokens"],
                            "total_tokens": self.api_usage["total_input_tokens"] + self.api_usage["total_output_tokens"],
                        },
                        "graph_stats": graph_stats,
                        "processing_time": {
                            "start": self.stats.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "end": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "duration_minutes": round(duration_minutes, 2),
                        },
                        # === INCREMENTAL: сохраняем списки обработанных слайсов/файлов ===
                        "processed_slice_ids": sorted(list(self.processed_slice_ids)),
                        "processed_source_files": sorted(list(self.processed_source_files)),
                    }
                }
            }
            
            output_data = {**metadata, "nodes": self.graph_nodes, "edges": self.graph_edges}
            
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] SUCCESS  | ✅ Results saved to /data/out/LearningChunkGraph_raw.json")
            print(f"                    | - Nodes: {len(self.graph_nodes)} | Edges: {len(self.graph_edges)}")
            return EXIT_SUCCESS
        
        except Exception as e:
            self.logger.error(f"Failed to save results: {e}")
            self._save_temp_dumps("io_error")
            return EXIT_IO_ERROR


def main():
    parser = argparse.ArgumentParser(description="Build knowledge graph from slices")
    parser.add_argument(
        "--incremental", action="store_true",
        help="Load existing graph and append new slices",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Force full rebuild (ignore existing graph)",
    )
    args = parser.parse_args()
    
    try:
        config = load_config(CONFIG_PATH)
        
        # === АВТООПРЕДЕЛЕНИЕ INCREMENTAL РЕЖИМА ===
        graph_path = OUTPUT_DIR / "LearningChunkGraph_raw.json"
        
        if args.full:
            # Явно запрошен full rebuild
            incremental = False
        elif args.incremental:
            # Явно запрошен incremental
            incremental = True
        elif graph_path.exists():
            # Граф уже существует — по умолчанию incremental
            incremental = True
            print("Auto-detected: incremental mode (existing graph found)")
            print("  Use --full flag to force full rebuild")
        else:
            # Графа нет — full rebuild
            incremental = False
        
        processor = SliceProcessor(config, incremental=incremental)
        exit_code = processor.run()
        sys.exit(exit_code)
    
    except KeyboardInterrupt:
        print("\n[INTERRUPT] Processing stopped by user")
        sys.exit(EXIT_RUNTIME_ERROR)
    except Exception as e:
        print("[FATAL] Unexpected error: " + str(e))
        sys.exit(EXIT_RUNTIME_ERROR)


if __name__ == "__main__":
    main()