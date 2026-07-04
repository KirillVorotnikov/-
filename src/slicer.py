#!/usr/bin/env python3
"""
Slicer - разбивает текстовые файлы на слайсы для последующей обработки
"""
from dotenv import load_dotenv
load_dotenv()

import argparse
import json
import logging
import os
import sys
import unicodedata
from pathlib import Path
from transformers import AutoTokenizer
from bs4 import BeautifulSoup
from unidecode import unidecode

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config import load_config
from src.utils.tokenizer import choice_the_tokenizer, find_safe_token_boundary_with_fallback
from src.utils.console_encoding import setup_console_encoding
from src.utils.exit_codes import EXIT_CONFIG_ERROR, EXIT_INPUT_ERROR, EXIT_IO_ERROR, EXIT_RUNTIME_ERROR, EXIT_SUCCESS, log_exit

# Отключаем предупреждения о symlinks на Windows
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

setup_console_encoding()

class InputError(Exception):
    pass

def setup_logging(log_level="info"):
    level_map = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}
    level = level_map.get(log_level.lower(), logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger = logging.getLogger()
    logger.setLevel(level)
    logger.addHandler(console_handler)

def validate_config_parameters(config):
    slicer_config = config.get("slicer", {})
    required_params = ["max_tokens", "soft_boundary_max_shift", "allowed_extensions"]
    
    for param in required_params:
        if param not in slicer_config:
            raise ValueError(f"Missing required parameter slicer.{param}")
    
    max_tokens = slicer_config["max_tokens"]
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError(f"slicer.max_tokens must be a positive integer, got: {max_tokens}")
    
    soft_boundary_max_shift = slicer_config["soft_boundary_max_shift"]
    if not isinstance(soft_boundary_max_shift, int) or soft_boundary_max_shift < 0:
        raise ValueError("slicer.soft_boundary_max_shift must be a non-negative integer")
    
    allowed_extensions = slicer_config["allowed_extensions"]
    if not isinstance(allowed_extensions, list) or not allowed_extensions:
        raise ValueError("slicer.allowed_extensions must be a non-empty list")

def create_slug(filename):
    # Создаем slug из имени файла (транслитерация кириллицы)
    name_without_ext = Path(filename).stem
    transliterated = unidecode(name_without_ext)
    slug = transliterated.lower().replace(" ", "_").replace(".", "_")
    return slug

def preprocess_text(text):
    # Нормализация текста и удаление HTML скриптов/стилей
    if not isinstance(text, str):
        raise ValueError("Input parameter must be a string")
    
    normalized_text = unicodedata.normalize("NFC", text)
    
    if "<script" in normalized_text.lower() or "<style" in normalized_text.lower():
        soup = BeautifulSoup(normalized_text, "html.parser")
        for script in soup.find_all("script"):
            script.decompose()
        for style in soup.find_all("style"):
            style.decompose()
        return str(soup)
    
    return normalized_text

def load_and_validate_file(file_path, allowed_extensions):
    # Проверяем расширение файла
    if file_path.suffix.lstrip(".").lower() not in [ext.lower() for ext in allowed_extensions]:
        raise InputError(f"Unsupported file extension: {file_path.suffix}")
    
    # Пытаемся прочитать с разной кодировкой
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, "r", encoding="cp1251") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="latin1") as f:
                content = f.read()
    
    content = preprocess_text(content)
    
    if not content.strip():
        raise InputError(f"Empty file detected: {file_path.name}")
    
    return content

def slice_text_with_window(text, max_tokens, soft_boundary, soft_boundary_max_shift, file_name=None, tokenizer_path=None, is_offline=True):
    # Разбивает текст на слайсы с учетом токенов
    if not text or not text.strip():
        return []
    
    # Передаём tokenizer_path в функцию выбора токенизатора
    encoding = choice_the_tokenizer(is_offline, tokenizer_path, None)
    
    # Если весь текст помещается в один слайс
    if len(text) <= max_tokens * 10:
        tokens = encoding.encode(text)
        if len(tokens) <= max_tokens:
            logging.info(f"File fits in single slice: {len(tokens)} tokens")
            return [(text, 0, len(tokens))]
    
    # Оценка общего количества токенов для больших файлов
    estimated_total_tokens = len(text) // 4
    estimated_slices = (estimated_total_tokens // max_tokens) + 1
    
    if file_name:
        file_size_mb = len(text) / (1024 * 1024)
        logging.info(f"Processing file: {file_name} ({file_size_mb:.1f}MB, ~{estimated_total_tokens:,} tokens)")
        logging.info(f"Estimated slices: ~{estimated_slices}")
    
    slices = []
    char_pos = 0
    global_token_offset = 0
    slice_num = 1
    
    while char_pos < len(text):
        # Размер окна с буфером
        window_chars = max_tokens * 10
        buffer_chars = soft_boundary_max_shift * 10 if soft_boundary else 0
        total_window_chars = window_chars + buffer_chars
        
        window_text = text[char_pos:char_pos + total_window_chars]
        if not window_text:
            break
        
        window_tokens = encoding.encode(window_text)
        
        # Если это последний слайс
        if len(window_tokens) <= max_tokens:
            slice_text = window_text
            slice_token_count = len(window_tokens)
            logging.info(f"Creating slice_{slice_num:03d} (tokens {global_token_offset}-{global_token_offset + slice_token_count}) [100%]")
            slices.append((slice_text, global_token_offset, global_token_offset + slice_token_count))
            break
        else:
            # Находим границу слайса
            target_pos = min(max_tokens, len(window_tokens))
            
            if soft_boundary and soft_boundary_max_shift > 0:
                boundary_token_pos = find_safe_token_boundary_with_fallback(
                    text=window_text, tokens=window_tokens, encoding=encoding,
                    target_token_pos=target_pos, max_shift_tokens=soft_boundary_max_shift,
                    max_tokens=max_tokens
                )
            else:
                boundary_token_pos = target_pos
            
            slice_text = encoding.decode(window_tokens[:boundary_token_pos])
            slice_token_count = boundary_token_pos
            progress_pct = (char_pos / len(text)) * 100
            logging.info(f"Creating slice_{slice_num:03d} (tokens {global_token_offset}-{global_token_offset + slice_token_count}) [{progress_pct:.0f}%]")
            
            # Проверка на пропуски
            if slices and global_token_offset != slices[-1][2]:
                logging.warning(f"Gap detected: previous ended at {slices[-1][2]}, current starts at {global_token_offset}")
            
            slices.append((slice_text, global_token_offset, global_token_offset + slice_token_count))
            char_pos += len(slice_text)
            global_token_offset += slice_token_count
            slice_num += 1
    
    if file_name:
        logging.info(f"Completed: {len(slices)} slices from {file_name}")
    
    return slices

def save_slice(slice_data, output_dir):
    # Сохраняет слайс в JSON файл
    slice_id = slice_data["id"]
    output_file = output_dir / f"{slice_id}.slice.json"
    
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(slice_data, f, ensure_ascii=False, indent=2)
        logging.info(f"Slice saved: {output_file}")
    except Exception as e:
        logging.error(f"Error saving slice {slice_id}: {e}")
        raise IOError(f"Failed to save slice {slice_id}: {e}")

def process_file(file_path, config, global_slice_counter):
    # Обрабатывает один файл: загружает, разбивает на слайсы
    slicer_config = config["slicer"]
    file_size = file_path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)
    logging.info(f"Processing file: {file_path.name} ({file_size_mb:.2f}MB)")
    
    # Получаем путь к токенизатору из конфига
    tokenizer_path = slicer_config.get("tokenizer_path") or slicer_config.get("tokenizer")
    
    try:
        content = load_and_validate_file(file_path, slicer_config["allowed_extensions"])
        slug = create_slug(file_path.name)
        
        # Передаём tokenizer_path в функцию слайсинга
        slices_data = slice_text_with_window(
            content, slicer_config["max_tokens"], slicer_config["soft_boundary"],
            slicer_config["soft_boundary_max_shift"], file_name=file_path.name,
            tokenizer_path=tokenizer_path, is_offline=True
        )
        
        slices = []
        for slice_text, slice_token_start, slice_token_end in slices_data:
            slice_obj = {
                "id": f"slice_{global_slice_counter:03d}",
                "order": global_slice_counter,
                "source_file": file_path.name,
                "slug": slug,
                "text": slice_text,
                "slice_token_start": slice_token_start,
                "slice_token_end": slice_token_end
            }
            slices.append(slice_obj)
            global_slice_counter += 1
        
        logging.info(f"File {file_path.name}: created {len(slices)} slices")
        return slices, global_slice_counter
    
    except InputError:
        raise
    except Exception as e:
        logging.error(f"Error processing file {file_path.name}: {e}")
        raise RuntimeError(f"Failed to process file {file_path.name}: {e}")
def clear_staging_directory(staging_dir):
    """
    Очищает staging директорию от старых слайсов.
    Это гарантирует, что каждый запуск slicer работает с чистого листа.
    """
    if not staging_dir.exists():
        return 0
    
    removed = 0
    for file_path in staging_dir.glob("*.slice.json"):
        try:
            file_path.unlink()
            removed += 1
        except Exception as e:
            logging.warning(f"Failed to remove {file_path.name}: {e}")
    
    if removed > 0:
        logging.info(f"Cleared {removed} old slice(s) from {staging_dir}")
    
    return removed

def main(argv=None):
    """Main slicer function."""
    parser = argparse.ArgumentParser(
        description="Utility for splitting educational texts into slices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Keep existing slices in staging (add new files without re-slicing old ones)",
    )
    args = parser.parse_args(argv)
    
    try:
        config = load_config()
        log_level = config.get("slicer", {}).get("log_level", "info")
        setup_logging(log_level)
        logging.info("Starting slicer.py")
        
        try:
            validate_config_parameters(config)
        except ValueError as e:
            logging.error(f"Configuration error: {e}")
            return EXIT_CONFIG_ERROR
        
        raw_dir = Path("data/raw")
        staging_dir = Path("data/staging")
        
        if not raw_dir.exists():
            logging.error(f"Directory {raw_dir} does not exist")
            return EXIT_INPUT_ERROR
        
        try:
            staging_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logging.error(f"Failed to create directory {staging_dir}: {e}")
            return EXIT_IO_ERROR
        # Очищаем старые слайсы, если не включён incremental режим
        if not args.incremental:
            clear_staging_directory(staging_dir)
        else:
            logging.info("Incremental mode: keeping existing slices in staging")
        
        # Сбор файлов для обработки
        allowed_extensions = config["slicer"]["allowed_extensions"]
        input_files = []
        
        for ext in allowed_extensions:
            pattern = f"*.{ext.lower()}"
            input_files.extend(raw_dir.glob(pattern))
            pattern_upper = f"*.{ext.upper()}"
            input_files.extend(raw_dir.glob(pattern_upper))
        
        input_files = sorted(set(input_files))
        
        if not input_files:
            logging.warning(f"No files found for processing in {raw_dir}")
            return EXIT_SUCCESS
        
        # Пропуск неподдерживаемых файлов
        all_files = list(raw_dir.iterdir())
        for file_path in all_files:
            if file_path.is_file() and file_path not in input_files:
                logging.warning(f"Unsupported file skipped: {file_path.name}")
        
        logging.info(f"Found {len(input_files)} files for processing")
        
        # Обработка файлов
        global_slice_counter = 1

        # === INCREMENTAL FIX: Start counter from max existing slice_id ===
        if args.incremental and staging_dir.exists():
            max_order = 0
            for slice_file in staging_dir.glob("*.slice.json"):
                try:
                    with open(slice_file, encoding="utf-8") as f:
                        data = json.load(f)
                    order = data.get("order", 0)
                    if order > max_order:
                        max_order = order
                except Exception:
                    pass
            
            if max_order > 0:
                global_slice_counter = max_order + 1
                logging.info(f"Incremental mode: starting slice counter from {global_slice_counter}")

        total_slices = 0
        for file_path in input_files:
            try:
                slices, global_slice_counter = process_file(file_path, config, global_slice_counter)
                
                for slice_data in slices:
                    save_slice(slice_data, staging_dir)
                
                total_slices += len(slices)
            
            except InputError as e:
                logging.error(f"Input data error in file {file_path.name}: {e}")
                return EXIT_INPUT_ERROR
            except IOError as e:
                logging.error(f"I/O error when processing {file_path.name}: {e}")
                return EXIT_IO_ERROR
            except RuntimeError as e:
                logging.error(f"Runtime error when processing {file_path.name}: {e}")
                return EXIT_RUNTIME_ERROR
            except Exception as e:
                logging.error(f"Unexpected error when processing {file_path.name}: {e}")
                return EXIT_RUNTIME_ERROR
        
        logging.info(f"Processing completed: {total_slices} slices saved in {staging_dir}")
        log_exit(logging.getLogger(), EXIT_SUCCESS)
        return EXIT_SUCCESS
    
    except Exception as e:
        logging.error(f"Critical error: {e}")
        log_exit(logging.getLogger(), EXIT_RUNTIME_ERROR)
        return EXIT_RUNTIME_ERROR

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)