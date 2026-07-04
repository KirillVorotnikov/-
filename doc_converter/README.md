# doc-convert — конвертация документов в Markdown/HTML

Модуль предобработки данных для системы **«Фабрика гипотез»** (RAG / Knowledge Graph в материаловедении).

Принимает разнородные источники — научные статьи, патенты, отчёты, датасеты, схемы — и превращает их в **единый чистый Markdown или HTML** с сохранением таблиц, изображений и **трейсабилити** к исходному файлу (для цитирования в сгенерированных гипотезах).

**Поддерживаемые форматы входа:** `.xlsx`, `.docx`, `.pdf`, `.png`, `.jpg`/`.jpeg`

**Форматы выхода:** `.md`, `.html` или оба сразу + sidecar `.meta.json`

---

## Содержание

1. [Быстрый старт](#быстрый-старт)
2. [Установка](#установка)
3. [Структура выходных файлов](#структура-выходных-файлов)
4. [CLI — справочник](#cli--справочник)
5. [Примеры по форматам](#примеры-по-форматам)
6. [Пакетная обработка](#пакетная-обработка)
7. [Использование как Python-библиотеки](#использование-как-python-библиотеки)
8. [Интеграция с пайплайном K2-18](#интеграция-с-пайплайном-k2-18)
9. [Переменные окружения](#переменные-окружения)
10. [Архитектура](#архитектура)
11. [Тестирование](#тестирование)
12. [Устранение неполадок](#устранение-неполадок)

---

## Быстрый старт

Из **корня репозитория** `nornikel_KG`:

```powershell
# 1. Виртуальное окружение (если ещё нет)
python -m venv .venv
.venv\Scripts\activate

# 2. Зависимости проекта + doc-converter
pip install -r requirements.txt
pip install -e ./doc_converter

# 3. Pandoc для DOCX (один раз, через winget/choco или с https://pandoc.org)
pandoc --version

# 4. Конвертация Excel → md + html
python -m doc_converter.cli convert "Задача 1\Хвосты КГМК.xlsx" --format both --output-dir ./out
```

Результат в `./out/`:

```
out/
├── Хвосты КГМК.md
├── Хвосты КГМК.html
└── Хвосты КГМК.meta.json
```

---

## Установка

### Вариант A — из корня репозитория (рекомендуется)

Все зависимости уже перечислены в корневом `requirements.txt`:

```powershell
cd d:\repos\nornikel_KG
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e ./doc_converter
```

### Вариант B — только модуль doc_converter

```powershell
cd d:\repos\nornikel_KG\doc_converter
pip install -e .
```

Для полного набора форматов из корневого `requirements.txt` всё равно удобнее ставить зависимости одной командой.

### Системные зависимости

| Инструмент | Нужен для | Как проверить |
|------------|-----------|---------------|
| **Python 3.11+** | всё | `python --version` |
| **pandoc** | DOCX | `pandoc --version` |
| **marker-pdf models** | PDF (скачаются при первом запуске) | первый `convert` PDF займёт несколько минут |
| **GROBID server** | опционально, метаданные статей (`--academic`) | `http://localhost:8070` |

### Что сознательно не требуется сейчас

- **VLM / OpenAI Vision** — отключён по умолчанию (`VLM_BACKEND=off`)
- Для схем и таблиц-картинок без VLM работают: эвристики OpenCV, `img2table`, встроенный **mock**-бэкенд (`--vlm-backend mock`) только для отладки

---

## Структура выходных файлов

На каждый обработанный документ создаётся до трёх артефактов.

### Markdown (`имя.md`)

Структурированный текст: заголовки, параграфы, pipe-таблицы, блоки Mermaid для схем, ссылки на изображения.

```markdown
## Данные

| Параметр | Значение |
| --- | --- |
| Cu | 1.25 |

![Схема флотации](media/page6_img1.png)
```

### HTML (`имя.html`)

Полноценная HTML5-страница с подключённым **Mermaid.js** — схемы рендерятся при открытии в браузере.

### Sidecar-метаданные (`имя.meta.json`)

Нужны downstream-системе для **цитирования источников** в гипотезах:

```json
{
  "source_file": "Хвосты КГМК.xlsx",
  "source_type": "xlsx",
  "processed_at": "2026-07-03T21:40:00Z",
  "status": "success",
  "elements": [
    {"index": 0, "type": "heading", "source_sheet": "Данные", "extraction_method": "openpyxl+pandas"},
    {"index": 1, "type": "table", "source_sheet": "Данные", "extraction_method": "openpyxl+pandas", "confidence": 1.0},
    {"index": 5, "type": "graph", "source_page": 6, "extraction_method": "vlm-mock", "confidence": 0.74, "raw_image_path": "media/Схема_6.png", "caption": "Схема флотационной машины"}
  ]
}
```

### Папка `media/`

Извлечённые из DOCX/PDF/изображений картинки копируются в `out/media/` с относительными путями в md/html.

---

## CLI — справочник

Точка входа:

```powershell
python -m doc_converter.cli --help
# или после установки пакета:
doc-convert --help
```

### Команда `convert` — один файл

```
doc-convert convert <input_path> [OPTIONS]
```

| Параметр | По умолчанию | Описание |
|----------|--------------|----------|
| `--format` | `md` | `md` \| `html` \| `both` |
| `--output-dir` | `./out` | Куда писать результаты |
| `--vlm-backend` | `off` | `off` \| `mock` (остальные — в разработке) |
| `--ocr-lang` | `ru` | Язык OCR для fallback-таблиц |
| `--academic` | `false` | Включить GROBID для PDF-статей |
| `--verbose` / `-v` | `false` | Подробные логи |

### Команда `batch` — вся папка

```
doc-convert batch <input_dir> [OPTIONS]
```

Дополнительно:

| Параметр | Описание |
|----------|----------|
| `--recursive` | Обходить подпапки |

---

## Примеры по форматам

Пути приведены относительно корня репозитория. Запускайте из `d:\repos\nornikel_KG`.

### XLSX — таблицы хвостов (самый быстрый сценарий)

Конвертирует **все листы** книги: каждый лист → заголовок + markdown/html-таблица с учётом merged cells.

```powershell
# Один файл
python -m doc_converter.cli convert "Задача 1\Хвосты КГМК.xlsx" `
  --format both `
  --output-dir ./out

# Другие датасеты из проекта
python -m doc_converter.cli convert "Задача 1\Хвосты НОФ мед.xlsx" --format both --output-dir ./out
python -m doc_converter.cli convert "Задача 1\Пример 1\Хвосты КГМК.xlsx" --format md --output-dir ./out/xlsx
```

**Что происходит внутри:** `pandas` читает данные, `openpyxl` корректно разворачивает объединённые ячейки.

---

### DOCX — гипотезы и отчёты

```powershell
# Убедитесь, что pandoc установлен
pandoc --version

python -m doc_converter.cli convert "Задача 1\Гипотезы КГМК.docx" `
  --format both `
  --output-dir ./out `
  --vlm-backend off

python -m doc_converter.cli convert "Задача 1\Как читать отчет института по хвостам.docx" `
  --format both `
  --output-dir ./out/docx
```

**Что происходит внутри:**

1. `pandoc -f docx -t markdown --extract-media=...` — текст, заголовки, таблицы, картинки
2. Markdown парсится в промежуточное представление (IR)
3. Встроенные изображения сохраняются в `out/media/`

---

### PDF — учебники и статьи по флотации

```powershell
# Технологический PDF (первый запуск скачает модели marker-pdf)
python -m doc_converter.cli convert "Задача 1\geokniga-flotacionnye-metody-obogashcheniya_0.pdf" `
  --format both `
  --output-dir ./out/pdf `
  --vlm-backend off

# Схемы в PDF
python -m doc_converter.cli convert "Задача 1\схемы флот++.pdf" `
  --format both `
  --output-dir ./out/pdf

# Академическая статья + GROBID (нужен запущенный сервер GROBID)
python -m doc_converter.cli convert "Задача 1\tehnologiya_izvlecheniya_zolota_i_serebra_iz_upornogo_zolotosoderzhaschego.pdf" `
  --format both `
  --output-dir ./out/pdf `
  --academic `
  --vlm-backend off
```

**Что происходит внутри:**

1. **marker-pdf** — OCR, заголовки, таблицы, текст постранично
2. **PyMuPDF** — извлечение embedded images с привязкой к номеру страницы
3. Эвристика «плохой таблицы» (>30% пустых ячеек) → перерисовка страницы в PNG → повторное извлечение таблицы
4. Картинки со страницы → `out/media/`, вставляются после контента страницы
5. `--academic` или автоопределение Abstract+References → опционально **GROBID** (title, author, DOI)

---

### PNG / JPG — схемы флотации и регламенты

Без VLM (рекомендуется сейчас) — изображение сохраняется как `image`-элемент с путём в `media/`:

```powershell
python -m doc_converter.cli convert "Задача 1\Схемы флотации\Схема флотации.png" `
  --format both `
  --output-dir ./out/schemes `
  --vlm-backend off
```

С **mock VLM** (для отладки pipeline схем/таблиц без API-ключей):

```powershell
python -m doc_converter.cli convert "Задача 1\Схемы флотации\Схема 3.png" `
  --format both `
  --output-dir ./out/schemes `
  --vlm-backend mock
```

При `mock` файлы с `diagram`/`table` в имени классифицируются соответственно и возвращают тестовые Mermaid/таблицы.

Пакет схем:

```powershell
python -m doc_converter.cli batch "Задача 1\Схемы флотации" `
  --format both `
  --output-dir ./out/schemes `
  --vlm-backend off
```

---

## Пакетная обработка

Все поддерживаемые файлы в папке (`.xlsx`, `.docx`, `.pdf`, `.png`, `.jpg`):

```powershell
# Только файлы в корне папки
python -m doc_converter.cli batch "Задача 1" `
  --format both `
  --output-dir ./out/batch `
  --vlm-backend off

# Рекурсивно, включая Пример 1/, Схемы флотации/ и т.д.
python -m doc_converter.cli batch "Задача 1" `
  --format both `
  --output-dir ./out/batch `
  --recursive `
  --vlm-backend off
```

При ошибке одного файла batch **не падает целиком** — пишет `status: failed` в `.meta.json` и продолжает со следующим.

---

## Использование как Python-библиотеки

### XLSX

```python
from pathlib import Path

from doc_converter.converters.xlsx_converter import XlsxConverter
from doc_converter.metadata import build_metadata, write_metadata
from doc_converter.renderers.html_renderer import render_html
from doc_converter.renderers.markdown_renderer import render_markdown

path = Path("Задача 1/Хвосты КГМК.xlsx")
document = XlsxConverter().parse(path)

md = render_markdown(document)
html = render_html(document)

out = Path("out")
out.mkdir(exist_ok=True)
(out / "Хвосты КГМК.md").write_text(md, encoding="utf-8")
(out / "Хвосты КГМК.html").write_text(html, encoding="utf-8")
write_metadata(out / "Хвосты КГМК.meta.json", build_metadata(document))
```

### DOCX

```python
from pathlib import Path

from doc_converter.config import Settings
from doc_converter.converters.docx_converter import DocxConverter
from doc_converter.renderers.markdown_renderer import render_markdown

settings = Settings(output_dir="out", VLM_BACKEND="off")
document = DocxConverter(settings).parse(Path("Задача 1/Гипотезы КГМК.docx"))
print(render_markdown(document))
```

### PDF

```python
from pathlib import Path

from doc_converter.config import Settings
from doc_converter.converters.pdf_converter import PdfConverter
from doc_converter.renderers.markdown_renderer import render_markdown

settings = Settings(output_dir="out", VLM_BACKEND="off", academic=False)
document = PdfConverter(settings).parse(
    Path("Задача 1/geokniga-flotacionnye-metody-obogashcheniya_0.pdf")
)

for element in document.elements:
    if element.source_page:
        print(f"[стр. {element.source_page}] {element.type}: {element.content[:80]}...")
```

### Изображение (PNG)

```python
from pathlib import Path

from doc_converter.config import Settings
from doc_converter.converters.image_converter import ImageConverter
from doc_converter.renderers.markdown_renderer import render_markdown

settings = Settings(output_dir="out", VLM_BACKEND="off")
document = ImageConverter(settings).parse(Path("Задача 1/Схема 1.png"))
print(render_markdown(document))
```

### Универсальный роутер (автовыбор конвертера)

```python
from pathlib import Path

from doc_converter.config import Settings
from doc_converter.router import parse_file
from doc_converter.renderers.markdown_renderer import render_markdown

settings = Settings(output_dir="out", VLM_BACKEND="off")

for file in [
    Path("Задача 1/Хвосты ТОФ_2.xlsx"),
    Path("Задача 1/Гипотезы ТОФ.docx"),
    Path("Задача 1/Схема 2.png"),
]:
    doc = parse_file(file, settings)
    print(f"=== {file.name} ({doc.source_type}) ===")
    print(render_markdown(doc)[:500])
```

---

## Интеграция с пайплайном K2-18

Типичный workflow «Фабрики гипотез»:

```
Задача 1/*.xlsx, *.docx, *.pdf, *.png
        ↓  doc-convert
out/*.md  (+ media/, *.meta.json)
        ↓  копировать тексты в data/raw/ или объединить
src/slicer.py → ConceptDictionary → LearningChunkGraph → ...
```

Пример подготовки markdown для слайсера:

```powershell
# 1. Конвертировать все материалы
python -m doc_converter.cli batch "Задача 1" --format md --output-dir ./out/md --recursive --vlm-backend off

# 2. Скопировать нужные .md в data/raw/ (вручную или скриптом)
#    Sidecar .meta.json храните рядом — пригодится для цитирования в гипотезах
```

---

## Переменные окружения

Скопируйте `doc_converter/.env.example` → `doc_converter/.env` или задайте в корне проекта:

```env
# Режим без нейросетей (рекомендуется сейчас)
VLM_BACKEND=off

# OCR для fallback-таблиц на картинках
OCR_LANG=ru

# GROBID (только для --academic PDF)
GROBID_SERVER=http://localhost:8070
```

| Переменная | Значение по умолчанию | Описание |
|------------|----------------------|----------|
| `VLM_BACKEND` | `off` | `off` — без VLM; `mock` — заглушка для тестов |
| `OCR_LANG` | `ru` | Язык OCR (`ru`, `en`, `rus+eng` для Tesseract/img2table) |
| `GROBID_SERVER` | — | URL сервера GROBID |

---

## Архитектура

```
Входной файл (.xlsx / .docx / .pdf / .png)
        ↓
    router.py  — определение типа
        ↓
    Конвертер формата → ParsedDocument (IR)
        ↓
    elements[]: heading | paragraph | table | image | graph | list | ...
        ↓
    renderers/ → .md и/или .html
    metadata.py → .meta.json
```

| Формат | Конвертер | Движок |
|--------|-----------|--------|
| XLSX | `xlsx_converter` | openpyxl + pandas |
| DOCX | `docx_converter` | pandoc + markdown parser |
| PDF | `pdf_converter` | marker-pdf + PyMuPDF |
| PNG/JPG | `image_converter` | image_pipeline (classifier, table/diagram extractors) |

Промежуточное представление (`ir.py`):

- `DocElement` — один блок документа с полями `source_page`, `source_sheet`, `extraction_method`, `confidence`, `raw_image_path`
- `ParsedDocument` — список элементов + метаданные источника

---

## Тестирование

```powershell
cd doc_converter
python -m pytest -v -m "not integration"
```

Интеграционные тесты (нужен pandoc):

```powershell
python -m pytest -v -m integration
```

---

## Устранение неполадок

### `pandoc not found` при конвертации DOCX

Установите pandoc и добавьте в PATH: https://pandoc.org/installing.html

```powershell
winget install JohnMacFarlane.Pandoc
pandoc --version
```

### PDF: долгий первый запуск

`marker-pdf` скачивает модели при первом вызове. Это нормально. Последующие запуски быстрее.

### PDF: `ImportError: marker-pdf`

```powershell
pip install marker-pdf pymupdf
# или из корня:
pip install -r requirements.txt
```

### Пустые или кривые таблицы в PDF

Конвертер автоматически детектирует «плохие» таблицы и пробует переизвлечь со страницы. Для сканов можно позже включить OCR-стек (`img2table` уже в requirements).

### Кириллица в путях на Windows

Используйте кавычки вокруг путей с пробелами и кириллицей:

```powershell
python -m doc_converter.cli convert "Задача 1\Хвосты КГМК.xlsx" --output-dir ./out
```

### Batch: файл пропущен с ошибкой

Смотрите `out/<имя>.meta.json` — поле `"status": "failed"` и `"error": "..."`. Запустите проблемный файл отдельно с `--verbose`.

---

## Структура пакета

```
doc_converter/
├── doc_converter/
│   ├── cli.py              # Typer CLI
│   ├── ir.py               # промежуточное представление
│   ├── router.py           # диспетчеризация по расширению
│   ├── config.py           # настройки
│   ├── metadata.py         # .meta.json
│   ├── converters/         # xlsx, docx, pdf, image
│   ├── image_pipeline/     # классификация и извлечение из картинок
│   ├── renderers/          # md + html (Jinja2 + Mermaid)
│   └── vlm/                # mock-заглушка (реальные бэкенды — позже)
├── tests/
├── examples/run_example.py
├── pyproject.toml
└── .env.example
```
