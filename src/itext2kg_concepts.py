#!/usr/bin/env python3
"""
iText2KG Concepts - извлекает концепты из текстовых слайсов используя LLM
"""
from dotenv import load_dotenv
load_dotenv()

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from transformers import AutoTokenizer, AutoModelForCausalLM

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config import load_config
from src.utils.console_encoding import setup_console_encoding
from src.utils.exit_codes import EXIT_CONFIG_ERROR, EXIT_INPUT_ERROR, EXIT_IO_ERROR, EXIT_RUNTIME_ERROR, EXIT_SUCCESS
from src.utils.llm_providers import LLMClientFactory
from src.utils.validation import ValidationError, validate_concept_dictionary_invariants, validate_json

setup_console_encoding()

CONFIG_PATH = Path(__file__).parent / "config.toml"
PROMPTS_DIR = Path(__file__).parent / "prompts"
SCHEMAS_DIR = Path(__file__).parent / "schemas"
STAGING_DIR = Path(__file__).parent.parent / "data" / "staging"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "out"
LOGS_DIR = Path(__file__).parent.parent / "logs"
EXTRACTION_PROMPT_FILE = "itext2kg_concepts_extraction_comm_v2-core-principles.md"

@dataclass
class ProcessingStats:
    total_slices: int = 0
    processed_slices: int = 0
    total_concepts: int = 0
    total_tokens_used: int = 0
    start_time: datetime = field(default_factory=lambda: datetime.now())

@dataclass
class SliceData:
    id: str
    order: int
    source_file: str
    slug: str
    text: str
    slice_token_start: int
    slice_token_end: int

class SliceProcessor:
    def __init__(self, config):
        self.config = config["itext2kg_concepts"]
        self.full_config = config
        self.llm_client = LLMClientFactory.create_client(self.config)
        self.logger = self._setup_logger()
        self.stats = ProcessingStats()
        
        # Аккумуляторы данных
        self.concept_dictionary = {"concepts": []}
        self.concept_id_map = {}  # concept_id -> index
        self.previous_response_id = None
        self.api_usage = {"total_requests": 0, "total_input_tokens": 0, "total_output_tokens": 0}
        
        self.extraction_prompt = self._load_extraction_prompt()
    
    def _format_tokens(self, tokens):
        # Форматирует число токенов в читаемый вид
        if tokens < 1000:
            return str(tokens)
        elif tokens < 1_000_000:
            return f"{tokens / 1000:.2f}k"
        else:
            return f"{tokens / 1_000_000:.2f}M"
    
    def _setup_logger(self):
        logger = logging.getLogger("itext2kg_concepts")
        logger.setLevel(getattr(logging, self.config["log_level"].upper()))
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = LOGS_DIR / f"itext2kg_concepts_{timestamp}.log"
        
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(file_handler)
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(console_handler)
        
        return logger
    
    def _load_extraction_prompt(self):
        # Загружает промпт и подставляет схему
        prompt_path = PROMPTS_DIR / EXTRACTION_PROMPT_FILE
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
        
        prompt_content = prompt_path.read_text(encoding="utf-8")
        concept_schema_path = SCHEMAS_DIR / "ConceptDictionary.schema.json"
        concept_schema = json.loads(concept_schema_path.read_text(encoding="utf-8"))
        prompt_content = prompt_content.replace("{concept_dictionary_schema}", json.dumps(concept_schema, indent=2))
        
        return prompt_content
    
    def _load_slice(self, slice_file):
        try:
            data = json.loads(slice_file.read_text(encoding="utf-8"))
            return SliceData(
                id=data["id"], order=data["order"], source_file=data["source_file"],
                slug=data["slug"], text=data["text"],
                slice_token_start=data["slice_token_start"], slice_token_end=data["slice_token_end"]
            )
        except (json.JSONDecodeError, KeyError) as e:
            raise ValueError(f"Invalid slice file {slice_file}: {e}")
    
    def _format_slice_input(self, slice_data):
        # Формирует JSON для отправки в LLM
        input_data = {
            "ConceptDictionary": self.concept_dictionary,
            "Slice": {
                "id": slice_data.id, "order": slice_data.order, "source_file": slice_data.source_file,
                "slug": slice_data.slug, "text": slice_data.text,
                "slice_token_start": slice_data.slice_token_start,
                "slice_token_end": slice_data.slice_token_end
            }
        }
        return json.dumps(input_data, ensure_ascii=False, indent=2)
    
    def _update_concept_dictionary(self, concepts_added):
        # Обновляет словарь концептов: добавляет новые, обновляет алиасы существующих
        for new_concept in concepts_added:
            concept_id = new_concept["concept_id"]
            
            if concept_id in self.concept_id_map:
                # Концепт существует - обновляем только алиасы
                idx = self.concept_id_map[concept_id]
                existing_concept = self.concept_dictionary["concepts"][idx]
                primary = existing_concept["term"].get("primary", "")
                primary_lower = primary.lower() if primary else None
                
                existing_aliases = existing_concept["term"].get("aliases", [])
                existing_lower_map = {}
                
                for alias in existing_aliases:
                    alias_lower = alias.lower()
                    if alias_lower != primary_lower:
                        existing_lower_map[alias_lower] = alias
                
                new_aliases = new_concept["term"].get("aliases", [])
                added_aliases = []
                
                for new_alias in new_aliases:
                    alias_lower = new_alias.lower()
                    if alias_lower not in existing_lower_map and alias_lower != primary_lower:
                        existing_lower_map[alias_lower] = new_alias
                        added_aliases.append(new_alias)
                
                if added_aliases:
                    existing_concept["term"]["aliases"] = sorted(existing_lower_map.values())
                    self.logger.debug(json.dumps({
                        "timestamp": datetime.now().isoformat(), "level": "DEBUG",
                        "event": "concept_update", "concept_id": concept_id,
                        "action": "added_aliases", "new_aliases": sorted(added_aliases)
                    }))
            else:
                # Новый концепт - очищаем алиасы от дубликатов
                primary = new_concept.get("term", {}).get("primary", "")
                aliases = new_concept.get("term", {}).get("aliases", [])
                
                if aliases and primary:
                    primary_lower = primary.lower()
                    seen_lower = {primary_lower: True}
                    unique_aliases = []
                    
                    for alias in aliases:
                        alias_lower = alias.lower()
                        if alias_lower not in seen_lower:
                            seen_lower[alias_lower] = True
                            unique_aliases.append(alias)
                    
                    new_concept["term"]["aliases"] = unique_aliases
                elif aliases:
                    seen_lower = {}
                    unique_aliases = []
                    
                    for alias in aliases:
                        alias_lower = alias.lower()
                        if alias_lower not in seen_lower:
                            seen_lower[alias_lower] = True
                            unique_aliases.append(alias)
                    
                    new_concept["term"]["aliases"] = unique_aliases
                
                # Добавляем концепт
                self.concept_dictionary["concepts"].append(new_concept)
                self.concept_id_map[concept_id] = len(self.concept_dictionary["concepts"]) - 1
                self.stats.total_concepts += 1
                
                self.logger.debug(json.dumps({
                    "timestamp": datetime.now().isoformat(), "level": "DEBUG",
                    "event": "concept_added", "concept_id": concept_id
                }))
    
    def _process_llm_response(self, response_text, slice_id):
        # Парсит JSON из ответа LLM
        try:
            response_data = json.loads(response_text)
            
            if "concepts_added" not in response_data:
                raise ValueError("Missing required field 'concepts_added'")
            
            concepts_added = response_data["concepts_added"].get("concepts", [])
            validate_json({"concepts": concepts_added}, "ConceptDictionary")
            return True, response_data
        
        except (json.JSONDecodeError, ValueError, ValidationError) as e:
            self.logger.error(json.dumps({
                "timestamp": datetime.now().isoformat(), "level": "ERROR",
                "event": "response_validation_failed", "slice_id": slice_id, "error": str(e)
            }))
            return False, None
    
    def _apply_concepts(self, response_data):
        # Применяет концепты из ответа LLM к словарю
        concepts_to_add = response_data["concepts_added"].get("concepts", [])
        self._update_concept_dictionary(concepts_to_add)
    
    def _save_bad_response(self, slice_id, original_response, error, repair_response=None):
        # Сохраняет неудачный ответ для анализа
        bad_response_file = LOGS_DIR / f"{slice_id}_bad.json"
        bad_data = {
            "slice_id": slice_id, "timestamp": datetime.now().isoformat(),
            "original_response": original_response, "validation_error": error,
            "repair_response": repair_response
        }
        bad_response_file.write_text(json.dumps(bad_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    def _save_temp_dumps(self, reason):
        # Сохраняет временные дампы при критических ошибках
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_concept_path = LOGS_DIR / f"ConceptDictionary_temp_{reason}_{timestamp}.json"
        
        if self.concept_dictionary and self.concept_dictionary.get("concepts"):
            temp_concept_path.write_text(
                json.dumps(self.concept_dictionary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"Temporary ConceptDictionary saved to: {temp_concept_path}", file=sys.stderr)
        
        stats_path = LOGS_DIR / f"processing_stats_{reason}_{timestamp}.json"
        stats_data = {
            "timestamp": datetime.now().isoformat(), "reason": reason,
            "stats": {
                "total_slices": self.stats.total_slices,
                "processed_slices": self.stats.processed_slices,
                "total_concepts": self.stats.total_concepts,
                "total_tokens_used": self.stats.total_tokens_used,
                "processing_time": str(datetime.now() - self.stats.start_time)
            }
        }
        stats_path.write_text(json.dumps(stats_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Processing stats saved to: {stats_path}", file=sys.stderr)
    
    def _process_single_slice(self, slice_file):
        # Обрабатывает один слайс с механизмом повтора при ошибках
        try:
            slice_data = self._load_slice(slice_file)
            self.logger.info(json.dumps({
                "timestamp": datetime.now().isoformat(), "level": "INFO",
                "event": "slice_start", "slice_id": slice_data.id,
                "order": slice_data.order, "total": self.stats.total_slices
            }))
            
            input_data = self._format_slice_input(slice_data)
            max_retries = self.config.get("max_retries", 3)
            last_error_type = None
            start_time = time.time()
            
            # Цикл повтора для восстановления после ошибок
            for attempt in range(max_retries + 1):
                try:
                    if attempt == 0:
                        # Первая попытка - обычный запрос
                        if self.config["log_level"].lower() == "debug":
                            self.logger.debug(json.dumps({
                                "timestamp": datetime.now().isoformat(), "level": "DEBUG",
                                "event": "llm_request", "slice_id": slice_data.id,
                                "prompt": self.extraction_prompt, "input_data": json.loads(input_data)
                            }))
                        
                        response_text, response_id, usage = self.llm_client.create_response(
                            instructions=self.extraction_prompt, input_data=input_data
                        )
                    else:
                        # Повтор через repair
                        current_time = datetime.now().strftime("%H:%M:%S")
                        print(f"[{current_time}] REPAIR   | 🔧 Attempt {attempt}/{max_retries} after {last_error_type}...")
                        
                        repair_hint = ""
                        if last_error_type == "json":
                            repair_hint = "\nCRITICAL: Return ONLY valid JSON. No markdown, no explanations."
                        elif last_error_type == "timeout":
                            repair_hint = "\nIMPORTANT: Be concise to avoid timeout. Focus on essential concepts only."
                        
                        response_text, response_id, usage = self.llm_client.create_response(
                            instructions=self.extraction_prompt + repair_hint, input_data=input_data
                        )
                    
                    # Отслеживание использования API
                    self.api_usage["total_requests"] += 1
                    self.api_usage["total_input_tokens"] += usage.input_tokens
                    self.api_usage["total_output_tokens"] += usage.output_tokens
                    
                    if self.config["log_level"].lower() == "debug":
                        self.logger.debug(json.dumps({
                            "timestamp": datetime.now().isoformat(), "level": "DEBUG",
                            "event": "llm_response", "slice_id": slice_data.id,
                            "response": response_text, "response_id": response_id,
                            "usage": {
                                "input_tokens": usage.input_tokens,
                                "output_tokens": usage.output_tokens,
                                "reasoning_tokens": usage.reasoning_tokens
                            }
                        }))
                    
                    # Валидация и парсинг JSON
                    success, parsed_data = self._process_llm_response(response_text, slice_data.id)
                    
                    if not success:
                        last_error_type = "json"
                        
                        if attempt == max_retries:
                            self._save_bad_response(slice_data.id, response_text, f"JSON validation failed after {max_retries} retries")
                            current_time = datetime.now().strftime("%H:%M:%S")
                            print(f"[{current_time}] ERROR    | ❌ {slice_data.order:03d}/{self.stats.total_slices:03d} | {slice_data.id} | JSON validation failed after {max_retries} retries")
                            print(f"[{current_time}] FAILED   | ❌ Cannot continue without slice {slice_data.id} - would break incremental context")
                            self.logger.error(f"JSON validation failed for slice {slice_data.id} after {max_retries} retries - stopping to preserve incremental context")
                            self._save_temp_dumps(reason="critical_slice_failure")
                            return False
                        
                        continue
                    
                    # Успех! Подтверждаем ответ
                    self.llm_client.confirm_response()
                    self._apply_concepts(parsed_data)
                    self.previous_response_id = response_id
                    self.stats.total_tokens_used += usage.total_tokens
                    
                    duration_sec = round(time.time() - start_time, 0)
                    duration_ms = int((time.time() - start_time) * 1000)
                    current_time = datetime.now().strftime("%H:%M:%S")
                    
                    tokens_used = self._format_tokens(self.stats.total_tokens_used)
                    tokens_current = self._format_tokens(usage.total_tokens)
                    tokens_info = f"tokens_used={tokens_used} | tokens_current={tokens_current}"
                    
                    if usage.reasoning_tokens > 0:
                        reasoning = self._format_tokens(usage.reasoning_tokens)
                        tokens_info += f" incl. reasoning={reasoning}"
                    
                    print(f"[{current_time}] SLICE    | ✅ {slice_data.order:03d}/{self.stats.total_slices:03d} | {tokens_info} | {duration_sec}s | concepts={len(self.concept_dictionary['concepts'])}")
                    
                    self.logger.info(json.dumps({
                        "timestamp": datetime.now().isoformat(), "level": "INFO",
                        "event": "slice_success", "slice_id": slice_data.id,
                        "tokens_used": usage.total_tokens, "duration_ms": duration_ms,
                        "concepts_total": len(self.concept_dictionary["concepts"])
                    }))
                    
                    return True
                
                except TimeoutError as e:
                    last_error_type = "timeout"
                    current_time = datetime.now().strftime("%H:%M:%S")
                    
                    if attempt == max_retries:
                        print(f"[{current_time}] ERROR    | ❌ {slice_data.order:03d}/{self.stats.total_slices:03d} | {slice_data.id} | Timeout after {max_retries} retries")
                        print(f"[{current_time}] FAILED   | ❌ Cannot continue without slice {slice_data.id} - would break incremental context")
                        self.logger.error(f"Timeout processing slice {slice_data.id} after {max_retries} retries: {e}")
                        self._save_temp_dumps(reason="timeout_failure")
                        return False
                    
                    wait_time = 30 * (attempt + 1)
                    print(f"[{current_time}] REPAIR   | ⏳ Timeout occurred, waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                
                except Exception as e:
                    current_time = datetime.now().strftime("%H:%M:%S")
                    self.logger.error(f"Unexpected error processing slice {slice_data.id}: {e}")
                    print(f"[{current_time}] ERROR    | ❌ {slice_data.order:03d}/{self.stats.total_slices:03d} | {slice_data.id} | Unexpected error: {type(e).__name__}")
                    print(f"[{current_time}] FAILED   | ❌ Cannot continue without slice {slice_data.id} - would break incremental context")
                    self._save_temp_dumps(reason="unexpected_error")
                    return False
            
            return False
        
        except Exception as e:
            self.logger.error(json.dumps({
                "timestamp": datetime.now().isoformat(), "level": "ERROR",
                "event": "slice_processing_error", "slice_file": str(slice_file), "error": str(e)
            }))
            return False
    
    def run(self):
        # Основной метод обработки всех слайсов
        try:
            slice_files = sorted(STAGING_DIR.glob("*.slice.json"))
            
            if not slice_files:
                self.logger.error("No slice files found in staging directory")
                return EXIT_INPUT_ERROR
            
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
            
            self._print_start_status()
            
            # Обработка слайсов
            for slice_file in slice_files:
                try:
                    success = self._process_single_slice(slice_file)
                    
                    if success:
                        self.stats.processed_slices += 1
                    else:
                        self.logger.error(f"Critical error processing slice {slice_file.stem} - stopping to preserve incremental context")
                        current_time = datetime.now().strftime("%H:%M:%S")
                        print(f"[{current_time}] FAILED   | ❌ Processing stopped - incremental context broken")
                        return EXIT_RUNTIME_ERROR
                    
                    if self.stats.processed_slices % 10 == 0 and self.stats.processed_slices > 0:
                        self.logger.info(json.dumps({
                            "timestamp": datetime.now().isoformat(), "level": "INFO",
                            "event": "progress_checkpoint", "processed": self.stats.processed_slices,
                            "total": self.stats.total_slices
                        }))
                
                except KeyboardInterrupt:
                    self.logger.warning("Processing interrupted by user")
                    
                    if self.stats.processed_slices > 0:
                        self.logger.info(f"Processed {self.stats.processed_slices}/{self.stats.total_slices} slices before interruption")
                        
                        try:
                            self._save_temp_dumps("interrupted")
                            self.logger.info("Partial results saved to logs directory")
                        except Exception as e:
                            self.logger.error(f"Failed to save partial results: {e}")
                    
                    return EXIT_RUNTIME_ERROR
                
                except Exception as e:
                    self.logger.error(f"Unexpected error processing {slice_file}: {e}")
            
            if self.stats.processed_slices == 0:
                self.logger.error("All slices failed processing")
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] FAILED   | ❌ All slices failed processing")
                print(f"[{current_time}] SAVING   | 💾 Attempting to save empty structures...")
                
                try:
                    self._save_temp_dumps("all_failed")
                    print(f"[{current_time}] INFO     | Check /logs/ for temporary files and diagnostics")
                except Exception as dump_error:
                    current_time = datetime.now().strftime("%H:%M:%S")
                    print(f"[{current_time}] ERROR    | ⚠️ Failed to save temp dumps: {dump_error}", file=sys.stderr)
                
                return EXIT_RUNTIME_ERROR
            
            return self._finalize_and_save()
        
        except Exception as e:
            self.logger.error(f"Critical error in run(): {e}")
            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"[{current_time}] FAILED   | ❌ Critical error: {str(e)[:50]}...")
            print(f"[{current_time}] SAVING   | 💾 Emergency dump of current state...")
            
            try:
                self._save_temp_dumps("critical_error")
                print(f"[{current_time}] INFO     | Check /logs/ for temporary files and diagnostics")
            except Exception as dump_error:
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] ERROR    | ⚠️ Failed to save emergency dumps: {dump_error}", file=sys.stderr)
            
            return EXIT_RUNTIME_ERROR
    
    def _print_start_status(self):
        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"[{current_time}] START    | {self.stats.total_slices} slices | model={self.config['model']} | tpm={self.config['tpm_limit'] // 1000}k")
    
    def _finalize_and_save(self):
        # Финальная валидация и сохранение результатов
        try:
            validate_json(self.concept_dictionary, "ConceptDictionary")
            validate_concept_dictionary_invariants(self.concept_dictionary)
            
            concepts_with_aliases = 0
            total_aliases = 0
            
            for concept in self.concept_dictionary.get("concepts", []):
                aliases = concept.get("term", {}).get("aliases", [])
                if aliases:
                    concepts_with_aliases += 1
                    total_aliases += len(aliases)
            
            total_concepts = len(self.concept_dictionary.get("concepts", []))
            avg_aliases = round(total_aliases / total_concepts, 2) if total_concepts > 0 else 0
            
            end_time = datetime.now()
            duration_minutes = (end_time - self.stats.start_time).total_seconds() / 60
            
            config = self.config.copy()
            slicer_config = self.full_config.get("slicer", {})
            
            metadata = {
                "_meta": {
                    "itext2kg_concepts": {
                        "generated_at": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "config": {
                            "model": config.get("model"),
                            "temperature": config.get("temperature"),
                            "max_output_tokens": config.get("max_completion"),
                            "reasoning_effort": config.get("reasoning_effort"),
                            "overlap": slicer_config.get("overlap", 0),
                            "slice_size": slicer_config.get("max_tokens", 5000)
                        },
                        "source": {
                            "total_slices": self.stats.total_slices,
                            "processed_slices": self.stats.processed_slices,
                            "total_tokens": self.total_source_tokens,
                            "slug": self.source_slug if hasattr(self, "source_slug") else "unknown"
                        },
                        "api_usage": {
                            "total_requests": self.api_usage["total_requests"],
                            "total_input_tokens": self.api_usage["total_input_tokens"],
                            "total_output_tokens": self.api_usage["total_output_tokens"],
                            "total_tokens": self.api_usage["total_input_tokens"] + self.api_usage["total_output_tokens"]
                        },
                        "concepts_stats": {
                            "total_concepts": total_concepts,
                            "concepts_with_aliases": concepts_with_aliases,
                            "total_aliases": total_aliases,
                            "avg_aliases_per_concept": avg_aliases
                        },
                        "processing_time": {
                            "start": self.stats.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "end": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "duration_minutes": round(duration_minutes, 2)
                        }
                    }
                }
            }
            
            output_data = {**metadata, **self.concept_dictionary}
            concept_path = OUTPUT_DIR / "ConceptDictionary.json"
            concept_path.write_text(json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8")
            
            self._print_end_status()
            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"[{current_time}] SUCCESS  | ✅ Results saved to /data/out/")
            print("                           | - ConceptDictionary.json")
            
            return EXIT_SUCCESS
        
        except ValidationError as e:
            self.logger.error(f"Validation failed: {e}")
            current_time = datetime.now().strftime("%H:%M:%S")
            print(f"[{current_time}] FAILED   | ❌ Validation failed: {str(e)[:50]}...")
            print(f"[{current_time}] SAVING   | 💾 Attempting to save partial results...")
            
            try:
                self._save_temp_dumps("validation_failed")
                print(f"[{current_time}] INFO     | Check /logs/ for temporary files and diagnostics")
            except Exception as dump_error:
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] ERROR    | ⚠️ Failed to save temp dumps: {dump_error}", file=sys.stderr)
            
            return EXIT_RUNTIME_ERROR
        
        except Exception as e:
            self.logger.error(f"Failed to save output files: {e}")
            
            try:
                self._save_temp_dumps("io_error")
            except Exception as dump_error:
                current_time = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time}] ERROR    | ⚠️ Failed to save temp dumps: {dump_error}", file=sys.stderr)
            
            return EXIT_IO_ERROR
    
    def _print_end_status(self):
        current_time = datetime.now().strftime("%H:%M:%S")
        duration = datetime.now() - self.stats.start_time
        minutes, seconds = divmod(int(duration.total_seconds()), 60)
        print(f"[{current_time}] END      | Done | slices={self.stats.processed_slices} | time={minutes}m {seconds}s")

def main():
    try:
        config = load_config(CONFIG_PATH)
        
        max_context = config["itext2kg_concepts"].get("max_context_tokens", 128000)
        if not isinstance(max_context, int) or max_context < 1000:
            raise ValueError(f"Invalid max_context_tokens: {max_context}. Must be integer >= 1000")
        
        max_context_test = config["itext2kg_concepts"].get("max_context_tokens_test", 128000)
        if not isinstance(max_context_test, int) or max_context_test < 1000:
            raise ValueError(f"Invalid max_context_tokens_test: {max_context_test}. Must be integer >= 1000")
        
        processor = SliceProcessor(config)
        return processor.run()
    
    except FileNotFoundError as e:
        print(f"Configuration file not found: {e}")
        return EXIT_CONFIG_ERROR
    except ValueError as e:
        print(f"Configuration error: {e}")
        return EXIT_CONFIG_ERROR
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return EXIT_CONFIG_ERROR

if __name__ == "__main__":
    sys.exit(main())