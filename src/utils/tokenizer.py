"""
tokenizer.py - умная токенизация с поиском семантических границ.
Поддерживает оффлайн (локальные веса) и онлайн (HuggingFace Hub) режимы.
"""
import logging
import os
import re
from transformers import AutoTokenizer

# Отключаем предупреждения о symlinks на Windows
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

logger = logging.getLogger(__name__)
TOKENIZER = None


def choice_the_tokenizer(is_offline=True, model_name=None, encoding_model=None):
    """
    Выбирает и инициализирует токенизатор.
    
    Args:
        is_offline: Если True - использует локальные веса (local_files_only=True)
        model_name: Путь к локальной модели или название HF-модели
        encoding_model: Альтернативная модель (fallback)
    """
    global TOKENIZER
    
    if TOKENIZER is None:
        # Если model_name не задан - пытаемся взять из глобального конфига
        tokenizer_path = model_name or encoding_model
        
        if not tokenizer_path:
            # Если путь не указан и is_offline=True - ошибка
            if is_offline:
                raise ValueError(
                    "tokenizer_path is required when is_offline=True. "
                    "Please specify tokenizer_path in config.toml [slicer] section."
                )
            # Fallback на базовый GPT-2 токенизатор (только если is_offline=False)
            tokenizer_path = "gpt2"
            logger.warning("No tokenizer path provided, falling back to 'gpt2'")
        
        logger.info(f"Loading tokenizer from: {tokenizer_path} (offline={is_offline})")
        
        try:
            TOKENIZER = AutoTokenizer.from_pretrained(
                tokenizer_path,
                trust_remote_code=True,
                local_files_only=is_offline
            )
        except Exception as e:
            logger.error(f"Failed to load tokenizer '{tokenizer_path}': {e}")
            # Если is_offline=True - не пытаемся качать из интернета
            if is_offline:
                raise ValueError(
                    f"Failed to load tokenizer '{tokenizer_path}' in offline mode. "
                    "Please check the path or download the model manually."
                )
            # Fallback на gpt2 (только если is_offline=False)
            logger.info("Falling back to default gpt2 tokenizer")
            TOKENIZER = AutoTokenizer.from_pretrained("gpt2")
    
    return TOKENIZER


def count_tokens(text):
    """Подсчитывает количество токенов в тексте."""
    if not isinstance(text, str):
        raise ValueError("Input parameter must be a string")
    
    global TOKENIZER
    if TOKENIZER is None:
        raise RuntimeError("Tokenizer not initialized. Call choice_the_tokenizer() first.")
    
    return len(TOKENIZER.encode(text, add_special_tokens=False))


def find_soft_boundary(text, target_pos, max_shift):
    """
    Находит ближайшую семантическую границу с учётом приоритетов.
    
    Приоритеты (меньше = важнее):
    1 - заголовки (HTML/Markdown/текстовые)
    2 - абзацы
    3 - предложения
    4 - фразы
    5 - слова
    """
    if not text or target_pos < 0 or target_pos > len(text) or max_shift < 0:
        return None
    
    start_pos = max(0, target_pos - max_shift)
    end_pos = min(len(text), target_pos + max_shift)
    
    # Структура для хранения кандидатов по типам границ
    boundary_types = {
        "section": {"weight": 1, "candidates": []},
        "paragraph": {"weight": 2, "candidates": []},
        "sentence": {"weight": 3, "candidates": []},
        "phrase": {"weight": 4, "candidates": []},
        "word": {"weight": 5, "candidates": []}
    }
    
    def add_matches(pattern, b_type, flags=0):
        """Добавляет все совпадения паттерна в соответствующий список."""
        for m in re.finditer(pattern, text, flags):
            if start_pos <= m.end() <= end_pos:
                boundary_types[b_type]["candidates"].append(m.end())
    
    # HTML-заголовки (h1-h6)
    add_matches(r"</h[1-6]>\s*(?=\n|$)", "section", re.I)
    # Markdown-заголовки
    add_matches(r"(?:^|\n)(#{1,6})\s+.*?(?=\n|$)", "section")
    # Текстовые заголовки (на разных языках)
    add_matches(
        r"(?:^|\n)(?:Глава|Параграф|Часть|Chapter|Section|Раздел|Урок|Тема)\s+.*?(?=\n|$)",
        "section", re.I | re.M
    )
    # Абзацы (двойные переносы, блоки кода, LaTeX, ссылки)
    add_matches(r"\n\n+", "paragraph")
    add_matches(r"(?:^|\n)```\s*(?=\n|$)", "paragraph")
    add_matches(r"(?:^|\n)\$\$\s*(?=\n|$)", "paragraph")
    add_matches(r"</a>|\]\([^)]+\)", "paragraph")
    
    # Предложения (с проверкой на сокращения типа "Dr.", "Prof.")
    abbreviations = (
        "Dr", "Mr", "Mrs", "Ms", "Prof", "St", "vs", "etc",
        "т.д", "т.п", "и.д", "и.п"
    )
    for m in re.finditer(r"[.!?]\s+", text):
        if start_pos <= m.end() <= end_pos:
            before = text[max(0, m.start() - 10):m.start()].strip()
            if not before.endswith(abbreviations):
                boundary_types["sentence"]["candidates"].append(m.end())
    
    # Фразы (точки с запятой, запятые, двоеточия, тире)
    add_matches(r";\s+", "sentence")
    add_matches(r",\s+", "phrase")
    add_matches(r":\s+", "phrase")
    add_matches(r"\s+[—–-]\s+", "phrase")
    add_matches(r"\s+", "word")
    
    # Выбираем лучшую границу по формуле: вес * расстояние (с небольшим штрафом за переход вперёд)
    best_boundary = None
    best_score = float("inf")
    
    for b_type, data in boundary_types.items():
        weight = data["weight"]
        for pos in set(data["candidates"]):
            # Небольшой штраф (0.9) за переход вперёд, чтобы предпочитать ранние границы
            score = weight * abs(pos - target_pos) * (0.9 if pos > target_pos else 1)
            if score < best_score:
                best_score = score
                best_boundary = pos
    
    return best_boundary


def _build_token_char_mapping(tokens, encoding):
    """Строит отображение индекс_токена -> позиция_символа."""
    return {i: len(encoding.decode(tokens[:i])) for i in range(len(tokens) + 1)}


def _get_text_at_boundary(decoded_text, char_pos):
    """Разделяет текст по позиции символа."""
    return decoded_text[:char_pos], decoded_text[char_pos:]


def find_boundary_candidates(decoded_text, target_char_pos, max_char_shift):
    """Находит кандидатов на границу с приоритетами."""
    candidates = []
    start_pos = max(0, target_char_pos - max_char_shift)
    end_pos = min(len(decoded_text), target_char_pos + max_char_shift)
    
    def add(pattern, priority, b_type, flags=0):
        for m in re.finditer(pattern, decoded_text, flags):
            if start_pos <= m.end() <= end_pos:
                candidates.append((m.end(), priority, b_type))
    
    # Заголовки (приоритет 1)
    add(r"(?:^|\n)(?=<h[1-6][^>]*>)", 1, "html_header")
    add(r"(?:^|\n)(?=#{1,6}\s+)", 1, "markdown_header")
    add(
        r"(?:^|\n)(?=(?:Глава|Параграф|Часть|Chapter|Section|Раздел|Урок|Тема)\s+)",
        1, "text_header", re.I
    )
    
    # Подзаголовки и абзацы (приоритет 2)
    add(r"(?:^|\n)(?=#{2,4}\s+)", 2, "subheader")
    add(r"\n\n+", 2, "paragraph")
    add(r"```\s*\n", 2, "code_block_end")
    
    # Предложения (приоритет 3)
    abbreviations = (
        "Dr", "Mr", "Mrs", "Ms", "Prof", "St", "vs", "etc",
        "т.д", "т.п", "и.д", "и.п"
    )
    for m in re.finditer(r"[.!?]\s+", decoded_text):
        if start_pos <= m.end() <= end_pos:
            before = decoded_text[max(0, m.start() - 10):m.start()]
            if not before.endswith(abbreviations):
                candidates.append((m.end(), 3, "sentence"))
    
    # Строки, фразы, слова (приоритеты 4-6)
    add(r"\n", 4, "line")
    add(r"[,;:]\s+", 5, "phrase")
    add(r"\s+", 6, "word")
    
    # Сортируем по приоритету и расстоянию до цели
    scored = sorted([(p * 1000 + abs(pos - target_char_pos), pos, b) for pos, p, b in candidates])
    return [(pos, b) for _, pos, b in scored[:50]]


def find_safe_token_boundary(text, tokens, encoding, target_token_pos, max_shift_tokens):
    """
    Находит безопасную границу разреза на уровне токенов.
    Проверяет, чтобы разрез не проходил через URL, ссылки, HTML-теги и т.д.
    """
    if not tokens:
        return 0, "empty"
    
    original_target = target_token_pos
    
    # Корректируем позицию, если она вне допустимого диапазона
    if target_token_pos < 0:
        if target_token_pos + max_shift_tokens >= 0:
            target_token_pos = 0
        else:
            return 0, "edge"
    elif target_token_pos > len(tokens):
        target_token_pos = len(tokens)
        original_target = target_token_pos
    
    # Извлекаем окно токенов вокруг целевой позиции
    start_pos = max(0, target_token_pos - max_shift_tokens)
    end_pos = min(len(tokens), target_token_pos + max_shift_tokens)
    working_tokens = tokens[start_pos:end_pos + 1]
    decoded_text = encoding.decode(working_tokens)
    
    # Строим двунаправленное отображение токен <-> символ
    token_to_char = {0: 0}
    for i in range(1, len(working_tokens)):
        token_to_char[i] = len(encoding.decode(working_tokens[:i]))
    token_to_char[len(working_tokens)] = len(decoded_text)
    
    char_to_token = {cp: lt for lt, cp in token_to_char.items()}
    target_local_token = target_token_pos - start_pos
    target_char_pos = token_to_char.get(target_local_token, len(decoded_text) // 2)
    
    # Находим кандидатов на границу
    candidates = find_boundary_candidates(decoded_text, target_char_pos, max_shift_tokens * 4)
    
    best_pos = target_token_pos
    best_score = float("inf")
    best_type = "none"
    
    priority_map = {
        "html_header": 1, "markdown_header": 1, "text_header": 1,
        "subheader": 2, "paragraph": 2, "code_block_end": 2,
        "sentence": 3, "line": 4, "phrase": 5, "word": 6
    }
    sorted_char_positions = sorted(char_to_token.keys())
    
    # Проверяем каждого кандидата
    if candidates:
        for char_pos, boundary_type in candidates:
            local_token = char_to_token.get(char_pos)
            
            # Если точного соответствия нет - ищем ближайший токен
            if local_token is None:
                for i, cp in enumerate(sorted_char_positions):
                    if cp >= char_pos:
                        if i > 0 and (cp - char_pos) > (char_pos - sorted_char_positions[i - 1]):
                            local_token = char_to_token[sorted_char_positions[i - 1]]
                        else:
                            local_token = char_to_token[cp]
                        break
                if local_token is None and sorted_char_positions:
                    local_token = char_to_token[sorted_char_positions[-1]]
            
            if local_token is None:
                continue
            
            global_pos = start_pos + local_token
            if abs(global_pos - original_target) > max_shift_tokens:
                continue
            
            actual_char_pos = token_to_char[local_token]
            
            # Проверяем, безопасен ли разрез в этой позиции
            if is_safe_cut_position(
                text_before=decoded_text[:actual_char_pos],
                text_after=decoded_text[actual_char_pos:]
            ):
                priority = priority_map.get(boundary_type, 7)
                score = priority * 1000 + abs(global_pos - target_token_pos)
                if score < best_score:
                    best_score = score
                    best_pos = global_pos
                    best_type = boundary_type
    
    # Если хорошей границы не найдено - ищем любую безопасную
    if best_type == "none":
        for local_pos in range(len(working_tokens) + 1):
            global_pos = start_pos + local_pos
            if abs(global_pos - original_target) > max_shift_tokens:
                continue
            char_pos = token_to_char[local_pos]
            if is_safe_cut_position(
                text_before=decoded_text[:char_pos],
                text_after=decoded_text[char_pos:]
            ):
                score = evaluate_boundary_quality(
                    text_before=decoded_text[:char_pos],
                    text_after=decoded_text[char_pos:]
                )
                total_score = score + abs(global_pos - target_token_pos) * 0.1
                if total_score < best_score:
                    best_score = total_score
                    best_pos = global_pos
                    best_type = "fallback"
    
    return best_pos, best_type


def find_safe_token_boundary_with_fallback(text, tokens, encoding, target_token_pos, max_shift_tokens, max_tokens):
    """
    Находит безопасную границу с умным fallback для больших блоков.
    Если в стандартном окне границы нет - расширяет поиск.
    """
    best_pos, best_type = find_safe_token_boundary(text, tokens, encoding, target_token_pos, max_shift_tokens)
    
    # Если нашли хорошую границу - возвращаем её
    if best_type not in ("none", "empty", "edge"):
        shift = best_pos - target_token_pos
        if shift:
            logger.info(f"Soft boundary found: shift {shift:+d} tokens")
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logger.debug(f"Boundary type: {best_type}")
        return best_pos
    
    # Проверяем fallback-позицию
    if best_pos != target_token_pos and 0 < best_pos < len(tokens):
        if is_safe_cut_position(
            text_before=encoding.decode(tokens[:best_pos]),
            text_after=encoding.decode(tokens[best_pos:])
        ):
            return best_pos
    
    # Расширяем поиск (до 30% от max_tokens)
    logger.warning(f"No safe boundary found at position {target_token_pos}, expanding search")
    extended_shift = int(max_tokens * 0.3)
    
    # Ищем вперёд
    for offset in range(max_shift_tokens + 1, extended_shift, 10):
        test_pos = min(len(tokens), target_token_pos + offset)
        if 0 < test_pos < len(tokens):
            if is_safe_cut_position(
                text_before=encoding.decode(tokens[:test_pos]),
                text_after=encoding.decode(tokens[test_pos:])
            ):
                logger.info(f"Extended boundary found: shift {test_pos - target_token_pos:+d} tokens")
                return test_pos
    
    # Ищем назад
    for offset in range(max_shift_tokens + 1, extended_shift, 10):
        test_pos = max(0, target_token_pos - offset)
        if 0 < test_pos < len(tokens):
            if is_safe_cut_position(
                text_before=encoding.decode(tokens[:test_pos]),
                text_after=encoding.decode(tokens[test_pos:])
            ):
                logger.info(f"Extended boundary found: shift {test_pos - target_token_pos:+d} tokens")
                return test_pos
    
    # Крайний случай - принудительная граница
    fallback_pos = min(len(tokens), target_token_pos + max_shift_tokens)
    logger.warning(f"Forcing boundary at position {fallback_pos} (no safe position found)")
    return fallback_pos


def is_safe_cut_position(text=None, tokens=None, encoding=None, pos=None, text_before=None, text_after=None):
    """
    Проверяет, безопасно ли резать текст в данной позиции.
    Проверяет: URL, markdown-ссылки, HTML-теги, формулы, блоки кода, списки, таблицы.
    """
    # Поддерживаем два способа вызова: через текст или через токены
    if text_before is None or text_after is None:
        if tokens is None or encoding is None or pos is None:
            raise ValueError("Either provide text_before/text_after or tokens/encoding/pos")
        if pos <= 0 or pos >= len(tokens):
            return pos in (0, len(tokens))
        text_before = encoding.decode(tokens[:pos])
        text_after = encoding.decode(tokens[pos:])
    
    return all([
        # Не режем внутри слова
        not (text_before and text_after and text_before[-1].isalnum() and text_after[0].isalnum()),
        not is_inside_url(text_before, text_after),
        not is_inside_markdown_link(text_before, text_after),
        not is_inside_html_tag(text_before, text_after),
        not is_inside_formula(text_before, text_after),
        not is_inside_code_block(text_before, text_after),
        not is_inside_list(text_before, text_after),
        not is_inside_table(text_before, text_after)
    ])


def is_inside_url(text_before, text_after):
    """Проверяет, находимся ли мы внутри URL."""
    if re.search(r"https?://[^\s)>]]*$", text_before):
        return bool(text_after and re.match(r"^[^\s)>]]+", text_after))
    return False


def is_inside_markdown_link(text_before, text_after):
    """Проверяет, находимся ли мы внутри markdown-ссылки [text](url)."""
    open_sq = text_before.count("[") - text_before.count("]")
    open_rnd = text_before.count("(") - text_before.count(")")
    
    if open_sq > 0:
        return True
    if text_before.endswith("]") and text_after.startswith("("):
        return True
    if "](h" in text_before[-10:] or (text_before.endswith("](") and open_rnd > 0):
        return True
    if text_before.endswith("]") and text_after and text_after[0] == "(":
        return True
    return False


def is_inside_html_tag(text_before, text_after):
    """Проверяет, находимся ли мы внутри HTML-тега."""
    return text_before.rfind("<") > text_before.rfind(">")


def is_inside_formula(text_before, text_after):
    """Проверяет, находимся ли мы внутри LaTeX-формулы ($...$)."""
    return text_before.count("$") % 2 == 1


def is_inside_code_block(text_before, text_after):
    """Проверяет, находимся ли мы внутри блока кода (```...```)."""
    return text_before.count("`") % 2 == 1


def is_inside_list(text_before, text_after):
    """Проверяет, находимся ли мы внутри списка (до 2 уровней вложенности)."""
    if not text_before or not text_after:
        return False
    
    lines_before = text_before.split("\n")[-3:]
    patterns = [
        r"^\d+\.\s+", r"^  \d+\.\s+", r"^  [a-z]\.\s+",
        r"^\t\d+\.\s+", r"^\t[a-z]\.\s+",
        r"^[-*+]\s+", r"^  [-*+]\s+", r"^\t[-*+]\s+",
        r"^•\s+", r"^  •\s+"
    ]
    
    for line in lines_before:
        if any(re.match(p, line) for p in patterns):
            first_line_after = text_after.split("\n")[0] if text_after.split("\n") else ""
            if any(re.match(p, first_line_after) for p in patterns):
                return True
    return False


def is_inside_table(text_before, text_after):
    """Проверяет, находимся ли мы внутри таблицы."""
    if not text_before or not text_after:
        return False
    
    lines_b = text_before.split("\n")[-5:]
    lines_a = text_after.split("\n")[:5]
    t_sep = r"^\s*|[\s:-]+|"
    t_row = r"^\s*|.*|"
    
    has_b = any(re.match(t_sep, l) or re.match(t_row, l) for l in lines_b)
    has_a = any(re.match(t_sep, l) or re.match(t_row, l) for l in lines_a)
    if has_b and has_a:
        return True
    
    # Проверяем HTML-таблицы
    html_before = text_before[-200:]
    return (html_before.count("<table") - html_before.count("</table>")) > 0


def evaluate_boundary_quality(text=None, tokens=None, encoding=None, pos=None, text_before=None, text_after=None):
    """
    Оценивает качество границы (меньше = лучше).
    Используется как fallback, когда приоритетные границы не найдены.
    """
    if text_before is not None and text_after is not None:
        context_before = text_before[-50:]
    elif tokens is not None and encoding is not None and pos is not None:
        if pos <= 0 or pos >= len(tokens):
            return 0.0
        context_before = encoding.decode(tokens[max(0, pos - 10):pos])
    else:
        raise ValueError("Either provide text_before/text_after or tokens/encoding/pos")
    
    # Заголовки - лучшие границы
    if re.search(r"</h[1-6]>\s*$", context_before, re.I):
        return 1.0
    if re.search(r"\n#{1,6}\s+.*$", context_before):
        return 1.0
    if re.search(r"\n(?:Глава|Chapter|Раздел)\s+.*$", context_before, re.I):
        return 1.0
    
    # Абзацы и предложения
    if context_before.endswith("\n\n"):
        return 5.0
    if re.search(r"[.!?]\s*$", context_before):
        return 10.0
    if context_before.endswith("\n"):
        return 15.0
    if re.search(r"[,;]\s*$", context_before):
        return 20.0
    if context_before.endswith("  "):
        return 50.0
    
    return 100.0