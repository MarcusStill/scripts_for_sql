"""
Скрипт приведения SQL-файлов к регламенту Greenplum.
Разработан на основании документа «Правила оформления Greenplum.docx».
"""

import os
import sys
import re
import argparse
from typing import Tuple, List

# Список ключевых слов SQL (нижний регистр)
SQL_KEYWORDS = {
    "select", "from", "where", "join", "insert", "update", "delete", "create", "alter",
    "group", "order", "having", "limit", "union", "with", "as", "on", "and", "or", "in",
    "not", "null", "is", "distinct", "case", "when", "then", "else", "end", "left", "right",
    "inner", "outer", "cross", "full", "merge", "truncate", "drop", "table", "index", "view",
    "function", "procedure", "declare", "begin", "if", "loop", "while", "for", "return",
    "execute", "commit", "rollback", "by", "into", "values", "exists", "between", "like",
    "ilike", "all", "any", "some", "true", "false", "cascade", "restrict", "replace",
    "distributed", "replicated", "randomly", "partition", "default", "primary", "key",
    "constraint", "check", "unique", "perform", "language", "plpgsql", "do", "returns",
    "comment", "column", "desc", "asc", "nulls", "first", "last",
    # Типы данных (SQLS-CAP-6)
    "int", "integer", "bigint", "smallint", "numeric", "decimal", "float", "double", "real",
    "char", "varchar", "text", "boolean", "date", "timestamp", "timestamptz", "bytea",
    "json", "jsonb", "uuid", "serial", "bigserial", "array", "interval",
    # Встроенные функции (SQLS-CAP-3)
    "coalesce", "nullif", "sum", "count", "avg", "min", "max", "row_number", "rank",
    "dense_rank", "lag", "lead", "first_value", "last_value", "cast", "convert",
    "substring", "trim", "upper", "lower", "length", "date_trunc", "extract",
    "to_date", "to_char", "to_number", "now", "current_date", "current_timestamp", "replace"
}

# Пользовательские функции КХД (CamelCase с лидирующим 'f' согласно SQLS-CAP-4)
CUSTOM_FUNC_MAP = {
    "fcrudtaskgroup": "fCrudTaskGroup",
    "fcrudobjectconstraints": "fCrudObjectConstraints",
    "fcrudregistredtaskarray": "fCrudRegistredTaskArray",
    "faddandrefreshflow": "fAddAndRefreshFlow",
    "fcrudregisteredtaskparams": "fCrudRegisteredTaskParams",
}

# Зарезервированные слова, которые нельзя случайно лишить кавычек
SQL_STRICT_RESERVED = {
    "user", "group", "order", "table", "column", "check", "constraint", "primary", "key",
    "select", "from", "where", "into", "all", "distinct", "limit", "offset", "with"
}

def unquote_safe_identifiers(sql_part: str) -> str:
    """Удаляет лишние двойные кавычки вокруг простых snake_case идентификаторов (SQLS-REF-4)."""
    def repl(m):
        ident = m.group(1)
        if ident.islower() and ident not in SQL_STRICT_RESERVED:
            return ident
        return m.group(0)

    return re.sub(r'"([a-z_][a-z0-9_]*)"', repl, sql_part)

def format_tokens_in_code_segment(segment: str) -> str:
    """Обрабатывает ключевые слова, функции, типы и операторы в чистом SQL коде."""
    # 1. Замена != на <> (SQLS-EXP-1)
    segment = segment.replace("!=", "<>")

    # 2. Убираем пробелы вокруг приведения типа :: (SQLS-LAY-16)
    segment = re.sub(r'\s*::\s*', '::', segment)

    # 3. CamelCase для пользовательских функций (SQLS-CAP-4)
    for func_lower, func_camel in CUSTOM_FUNC_MAP.items():
        segment = re.sub(r'\b' + re.escape(func_lower) + r'\b', func_camel, segment, flags=re.IGNORECASE)

    # 4. Приведение SQL-ключевых слов к нижнему регистру (SQLS-CAP-1, SQLS-CAP-5, SQLS-CAP-6)
    def word_repl(match):
        w = match.group(0)
        lw = w.lower()
        if lw in SQL_KEYWORDS:
            return lw
        return w

    segment = re.sub(r'\b[A-Za-z_][A-Za-z0-9_]*\b', word_repl, segment)

    # 5. Снятие кавычек с простых идентификаторов (SQLS-REF-4)
    segment = unquote_safe_identifiers(segment)

    return segment

def tokenize_and_transform_sql(content: str) -> str:
    """
    Разбивает файл на защищенные блоки (литералы '...', комментарии -- и /* */, доллар-квоты $$)
    и форматирует только реальный SQL код.
    """
    token_pattern = re.compile(
        r"('(?:''|[^'])*')|"                   # Одинарные кавычки (строковые литералы)
        r"(--[^\r\n]*)|"                     # Однострочные комментарии
        r"(/\*[\s\S]*?\*/)|"               # Многострочные комментарии
        r"(\$\$[\s\S]*?\$\$)|"           # Dollar-quoting анонимных блоков
        r"(\$\w+\$[\s\S]*?\$\w+\$)"    # Именованные dollar-quoting ($primary_key$...)
    )

    last_end = 0
    chunks = []

    for match in token_pattern.finditer(content):
        start, end = match.span()
        if start > last_end:
            chunks.append(format_tokens_in_code_segment(content[last_end:start]))

        token = match.group(0)
        # Если это dollar-quoted блок (тело PL/pgSQL), форматируем внутренний SQL
        if (token.startswith("$$") and token.endswith("$$")) or (token.startswith("$") and "$" in token[1:]):
            tag_match = re.match(r"^(\$[a-zA-Z0-9_]*\$)([\s\S]*)(\$[a-zA-Z0-9_]*\$)$", token)
            if tag_match:
                open_tag, body, close_tag = tag_match.groups()
                formatted_body = tokenize_and_transform_sql(body)
                chunks.append(f"{open_tag.lower()}{formatted_body}{close_tag.lower()}")
            else:
                chunks.append(token)
        else:
            chunks.append(token)

        last_end = end

    if last_end < len(content):
        chunks.append(format_tokens_in_code_segment(content[last_end:]))

    return "".join(chunks)

def align_create_table_ddl(text: str) -> str:
    """
    Форматирует DDL создания таблиц (create table):
    - Отступ 4 пробела для списка колонок (SQLS-IND-1)
    - Выравнивание типов колонок
    - Запятые в конце строки (trailing, SQLS-LAY-10)
    - Параметры with (...) и distributed by (...) в нижнем регистре
    """
    ct_pattern = re.compile(
        r'(create\s+table\s+(?:if\s+not\s+exists\s+)?)([a-zA-Z0-9_\.]+)\s*\(([\s\S]*?)\)\s*(with\s*\([^\)]*\))?\s*(distributed\s+by\s*\([^\)]*\))?\s*;',
        re.IGNORECASE
    )

    def _format_ct(match):
        header = match.group(1).lower().strip()
        tbl_name = match.group(2).strip()
        body = match.group(3)
        with_clause = match.group(4)
        dist_clause = match.group(5)

        raw_lines = body.splitlines()
        cols = []
        for line in raw_lines:
            s = line.strip()
            if not s:
                continue
            has_comma = s.endswith(',')
            col_expr = s[:-1].strip() if has_comma else s
            parts = col_expr.split(None, 1)
            if len(parts) == 2:
                col_name, col_type = parts[0], parts[1]
                if col_name.startswith('"') and col_name.endswith('"'):
                    col_name = col_name[1:-1]
                cols.append((col_name, col_type.lower()))
            else:
                cols.append((col_expr, ""))

        if cols:
            max_col_len = max(len(c[0]) for c in cols if c[1])
            col_width = max(max_col_len + 1, 32)
            total = len(cols)
            formatted_lines = []
            for i, (name, c_type) in enumerate(cols):
                comma = "," if i < total - 1 else ""
                if c_type:
                    formatted_lines.append(f"    {name:<{col_width}}{c_type}{comma}")
                else:
                    formatted_lines.append(f"    {name}{comma}")
            body_formatted = "\n".join(formatted_lines)
        else:
            body_formatted = body

        res = f"{header} {tbl_name}(\n{body_formatted}\n)"
        if with_clause:
            res += f"\n{with_clause.lower()}"
        if dist_clause:
            dist_clean = unquote_safe_identifiers(dist_clause.lower())
            dist_clean = re.sub(r'distributed\s+by\s*\(', 'distributed by (', dist_clean)
            res += f"\n{dist_clean}"
        res += "\n;"
        return res

    return ct_pattern.sub(_format_ct, text)

def clean_whitespaces_and_empty_lines(text: str) -> str:
    """
    SQLS-IND-1: Замена табов на 4 пробела.
    SQLS-LAY-14: Точка с запятой в конце строки без пробела перед ней.
    SQLS-LAY-23: Не более 2 пустых строк между операторами.
    SQLS-LAY-24: Не более 1 пустой строки внутри оператора.
    """
    text = text.replace('\t', '    ')
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r'[ \t]+;', ';', text)
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    return text.strip() + "\n"

def process_file_content(content: str) -> str:
    """Главная процедура комплексной обработки содержимого SQL."""
    res = content.replace('\t', '    ')
    res = tokenize_and_transform_sql(res)
    res = align_create_table_ddl(res)
    res = clean_whitespaces_and_empty_lines(res)
    return res

def process_directory(root_dir: str, dry_run: bool = False, verbose: bool = True) -> Tuple[int, int]:
    """Рекурсивный обход корневого каталога и обработка всех .sql файлов."""
    scanned_count = 0
    modified_count = 0

    print(f"[*] Начало сканирования каталога: {os.path.abspath(root_dir)}")
    if dry_run:
        print("[!] Режим DRY-RUN (файлы не будут перезаписаны)")

    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.lower().endswith(".sql"):
                scanned_count += 1
                filepath = os.path.join(dirpath, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        original_content = f.read()

                    formatted_content = process_file_content(original_content)

                    if formatted_content != original_content:
                        modified_count += 1
                        rel_path = os.path.relpath(filepath, root_dir)
                        if verbose:
                            print(f"[MODIFIED] {rel_path}")
                        if not dry_run:
                            with open(filepath, 'w', encoding='utf-8') as f:
                                f.write(formatted_content)
                except UnicodeDecodeError:
                    try:
                        with open(filepath, 'r', encoding='cp1251') as f:
                            original_content = f.read()
                        formatted_content = process_file_content(original_content)
                        if formatted_content != original_content:
                            modified_count += 1
                            rel_path = os.path.relpath(filepath, root_dir)
                            if verbose:
                                print(f"[MODIFIED (cp1251->utf-8)] {rel_path}")
                            if not dry_run:
                                with open(filepath, 'w', encoding='utf-8') as f:
                                    f.write(formatted_content)
                    except Exception as e:
                        print(f"[ERROR] Ошибка чтения {filepath}: {e}", file=sys.stderr)
                except Exception as e:
                    print(f"[ERROR] Ошибка обработки {filepath}: {e}", file=sys.stderr)

    return scanned_count, modified_count

def main():
    parser = argparse.ArgumentParser(
        description="Форматирование SQL-скриптов под регламент Greenplum КХД."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Корневая директория для поиска .sql файлов (по умолчанию: текущая папка)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Запустить проверку без изменения файлов на диске"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Минимальный вывод информации"
    )

    args = parser.parse_args()

    scanned, modified = process_directory(
        root_dir=args.directory,
        dry_run=args.dry_run,
        verbose=not args.quiet
    )

    print("-" * 50)
    print(f"Готово. Всего SQL-файлов найдено: {scanned}")
    print(f"Файлов изменено/требует правок: {modified}")

if __name__ == "__main__":
    main()