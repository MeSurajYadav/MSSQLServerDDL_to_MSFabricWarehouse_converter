#!/usr/bin/env python3
"""
Convert a SQL Server DDL script exported from SSMS into a Microsoft Fabric
Warehouse-oriented DDL script.

Usage:
    python convert_sqlserver_to_fabric.py sql_server_compatible.txt ms_fabric_warehouse_compatible.txt

What this handles:
    - Removes SQL Server-only batch/session/filegroup/index-option syntax.
    - Converts unsupported Fabric Warehouse persisted data types.
    - Converts datetime2(7) to datetime2(6).
    - Converts nvarchar(n) to varchar(n*2).
    - Converts inline PRIMARY KEY constraints into Fabric-compatible ALTER TABLE
      statements using NONCLUSTERED NOT ENFORCED.
    - Drops DEFAULT constraints, because Fabric Warehouse does not support them.
    - Removes SQL Server SPARSE column keyword.
    - Renames schema/object names containing backslash or slash by replacing those
      characters with underscore.

Review the generated .report.txt file before production use.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path


IDENTIFIER_RE = re.compile(r"\[([^\]]+)\]")


class ConversionReport:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.messages: list[str] = []

    def inc(self, key: str, amount: int = 1) -> None:
        self.counts[key] += amount

    def add(self, message: str) -> None:
        self.messages.append(message)

    def write(self, path: Path) -> None:
        lines = ["SQL Server to Microsoft Fabric Warehouse conversion report", ""]
        lines.append("Counts:")
        for key, value in sorted(self.counts.items()):
            lines.append(f"- {key}: {value}")
        if self.messages:
            lines.extend(["", "Notes:"])
            lines.extend(f"- {msg}" for msg in self.messages)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sanitize_bracket_identifier(match: re.Match[str], report: ConversionReport) -> str:
    name = match.group(1)
    new_name = name.replace("\\", "_").replace("/", "_")
    if new_name.endswith("."):
        new_name = new_name.rstrip(".") + "_"
    if new_name != name:
        report.inc("identifiers_renamed")
        report.add(
            f"Renamed identifier [{name}] to [{new_name}] because Fabric schema/table "
            f"names cannot contain / or \\ or end with dot."
        )
    return f"[{new_name}]"


def sanitize_identifiers(sql: str, report: ConversionReport) -> str:
    return IDENTIFIER_RE.sub(lambda m: sanitize_bracket_identifier(m, report), sql)


def strip_block_comments(sql: str) -> str:
    return re.sub(r"/\*.*?\*/", "", sql, flags=re.S)


def remove_sparse_keyword(sql: str, report: ConversionReport) -> str:
    """
    Removes SQL Server SPARSE column attribute.

    Example:
        [blobvalue] varbinary(max) SPARSE  NULL
    becomes:
        [blobvalue] varbinary(max) NULL
    """
    new_sql, count = re.subn(
        r"(?i)\s+\bSPARSE\b(?=\s+(?:NULL|NOT\s+NULL|,|\r?\n))",
        "",
        sql,
    )
    if count:
        report.inc("sparse_keywords_removed", count)
    return new_sql



# def double_nvarchar_length(args: str) -> str:
#     """
#     Converts nvarchar(n) length to varchar(n*2).
#     Keeps nvarchar(max) as varchar(max).

#     Example:
#         nvarchar(255) -> varchar(510)
#         nvarchar(max) -> varchar(max)
#     """
#     args_clean = re.sub(r"\s+", "", args).lower()

#     if args_clean == "(max)":
#         return "(max)"

#     match = re.match(r"^\((\d+)\)$", args_clean)
#     if not match:
#         return args

#     new_length = int(match.group(1)) * 2
#     return f"({new_length})"

def double_unicode_text_length(args: str) -> str:
    """
    Converts nchar(n)/nvarchar(n) length to char/varchar(n*2).
    Keeps max as max.
    """
    args_clean = re.sub(r"\s+", "", args).lower()

    if args_clean == "(max)":
        return "(max)"

    match = re.match(r"^\((\d+)\)$", args_clean)
    if not match:
        return args

    new_length = int(match.group(1)) * 2
    return f"({new_length})"


def split_batches(sql: str) -> list[str]:
    return re.split(r"(?im)^\s*GO\s*$", sql)


def remove_use_and_set_batches(batch: str, report: ConversionReport) -> str | None:
    stripped = batch.strip()
    if not stripped:
        return None

    if re.match(r"(?is)^USE\s+", stripped):
        report.inc("use_statements_removed")
        return None

    if re.match(r"(?is)^SET\s+(ANSI_NULLS|QUOTED_IDENTIFIER)\s+", stripped):
        report.inc("set_statements_removed")
        return None

    return batch


def rewrite_schema(batch: str, report: ConversionReport) -> str:
    if re.match(r"(?is)^\s*CREATE\s+SCHEMA\b", batch):
        report.inc("create_schema")
        return batch.strip().rstrip(";") + ";"
    return batch


def rewrite_data_types(line: str, report: ConversionReport) -> str:
    column_match = re.match(
        r"(?is)^(\s*\[[^\]]+\]\s+)"
        r"(\[[A-Za-z0-9_]+\]|[A-Za-z0-9_]+)"
        r"(\s*\(\s*(?:max|\d+(?:\s*,\s*\d+)?)\s*\))?"
        r"(.*)$",
        line,
    )
    if not column_match:
        return line

    prefix, raw_type, raw_args, suffix = column_match.groups()
    type_name = raw_type.strip("[]").lower()
    args = (raw_args or "").strip()
    args_clean = re.sub(r"\s+", "", args).lower()

    new_type = raw_type.strip("[]")
    counter_name: str | None = None

    if type_name == "ntext":
        new_type, args, counter_name = "varchar", "(max)", "ntext_to_varchar_max"

    elif type_name == "image":
        new_type, args, counter_name = "varbinary", "(max)", "image_to_varbinary_max"

    elif type_name in {"money", "smallmoney"}:
        new_type, args, counter_name = "decimal", "(19,4)", "money_to_decimal_19_4"

    elif type_name == "datetimeoffset":
        new_type, args, counter_name = "datetime2", "(6)", "datetimeoffset_to_datetime2_6"

    elif type_name == "datetime":
        new_type, args, counter_name = "datetime2", "(6)", "datetime_to_datetime2_6"

    elif type_name == "tinyint":
        new_type, args, counter_name = "smallint", "", "tinyint_to_smallint"

    elif type_name == "datetime2" and args_clean == "(7)":
        new_type, args, counter_name = "datetime2", "(6)", "datetime2_7_to_datetime2_6"

    # elif type_name == "nvarchar":
    #     new_type = "varchar"
    #     args = double_nvarchar_length(args)
    #     counter_name = (
    #         "nvarchar_max_to_varchar_max"
    #         if args_clean == "(max)"
    #         else "nvarchar_to_varchar_length_doubled"
    #     )

    elif type_name == "nvarchar":
        new_type = "varchar"
        args = double_unicode_text_length(args)
        counter_name = (
            "nvarchar_max_to_varchar_max"
            if args_clean == "(max)"
            else "nvarchar_to_varchar_length_doubled"
        )

    # elif type_name == "nchar":
    #     new_type = "char"
    #     counter_name = "nchar_to_char"
    elif type_name == "nchar":
        new_type = "char"
        args = double_unicode_text_length(args)
        counter_name = "nchar_to_char_length_doubled"
        
    elif type_name == "binary":
        new_type = "varbinary"
        counter_name = "binary_to_varbinary"

    elif type_name == "uniqueidentifier":
        # Fabric supports uniqueidentifier in Warehouse. Remove brackets only.
        new_type = "uniqueidentifier"

    else:
        supported_types = {
            "bigint",
            "bit",
            "char",
            "date",
            "datetime2",
            "decimal",
            "float",
            "int",
            "numeric",
            "real",
            "smallint",
            "time",
            "varbinary",
            "varchar",
        }
        if type_name in supported_types:
            new_type = type_name

    if counter_name:
        report.inc(counter_name)

    return f"{prefix}{new_type}{args}{suffix}"


def split_top_level_comma_items(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    i = 0

    while i < len(text):
        ch = text[i]
        current.append(ch)

        if ch == "'":
            if i + 1 < len(text) and text[i + 1] == "'":
                current.append(text[i + 1])
                i += 1
            else:
                in_string = not in_string

        elif not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                current.pop()
                items.append("".join(current).strip())
                current = []

        i += 1

    tail = "".join(current).strip()
    if tail:
        items.append(tail)

    return items


def extract_create_table(batch: str) -> tuple[str, str, str] | None:
    match = re.match(r"(?is)^\s*CREATE\s+TABLE\s+(.+?)\s*\(", batch)
    if not match:
        return None

    table_name = match.group(1).strip()
    open_pos = batch.find("(", match.end() - 1)

    depth = 0
    in_string = False
    close_pos = -1

    for i in range(open_pos, len(batch)):
        ch = batch[i]

        if ch == "'":
            if i + 1 < len(batch) and batch[i + 1] == "'":
                continue
            in_string = not in_string

        elif not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_pos = i
                    break

    if close_pos == -1:
        return None

    body = batch[open_pos + 1 : close_pos]
    suffix = batch[close_pos + 1 :]

    return table_name, body, suffix


def clean_pk_columns(raw_columns: str) -> str:
    columns = []

    for col in split_top_level_comma_items(raw_columns):
        col = re.sub(r"\bASC\b|\bDESC\b", "", col, flags=re.I)
        col = col.strip()
        if col:
            columns.append(col)

    return ", ".join(columns)


def rewrite_create_table(batch: str, report: ConversionReport) -> tuple[str, list[str]]:
    parsed = extract_create_table(batch)
    if parsed is None:
        return batch.strip().rstrip(";") + ";", []

    table_name, body, suffix = parsed
    report.inc("create_table")

    pk_alters: list[str] = []
    output_items: list[str] = []

    for item in split_top_level_comma_items(body):
        normalized = " ".join(item.split())

        pk_match = re.match(
            r"(?is)^(?:CONSTRAINT\s+(\[[^\]]+\]|\S+)\s+)?PRIMARY\s+KEY\s+"
            r"(?:CLUSTERED|NONCLUSTERED)?\s*\((.*?)\)\s*"
            r"(?:WITH\s*\(.*?\))?\s*(?:ON\s+\[[^\]]+\])?\s*$",
            normalized,
        )

        if pk_match:
            constraint_name = pk_match.group(1)

            if not constraint_name:
                plain_table_name = re.sub(r"[\[\]]", "", table_name.split(".")[-1])
                plain_table_name = re.sub(r"[^A-Za-z0-9_]+", "_", plain_table_name)
                constraint_name = f"[PK_{plain_table_name}]"
                report.inc("unnamed_primary_keys_named")

            columns = clean_pk_columns(pk_match.group(2))

            pk_alters.append(
                f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} "
                f"PRIMARY KEY NONCLUSTERED ({columns}) NOT ENFORCED;"
            )

            report.inc("primary_keys_moved_to_alter_table")

            if re.search(r"\bCLUSTERED\b", normalized, flags=re.I):
                report.inc("clustered_primary_keys_converted_to_nonclustered")

            continue

        rewritten = rewrite_data_types(item, report)
        output_items.append(rewritten.strip())

    create_sql = "CREATE TABLE " + table_name + " (\n"
    create_sql += ",\n".join("    " + item for item in output_items)
    create_sql += "\n);"

    if re.search(
        r"\bON\s+\[PRIMARY\]|\bTEXTIMAGE_ON\s+\[PRIMARY\]|\bWITH\s*\(",
        suffix,
        flags=re.I,
    ):
        report.inc("create_table_storage_or_index_options_removed")

    return create_sql, pk_alters


def rewrite_alter_table(batch: str, report: ConversionReport) -> str | None:
    stripped = batch.strip().rstrip(";")

    if re.match(r"(?is)^ALTER\s+TABLE\b", stripped) and re.search(
        r"(?is)\bDEFAULT\b", stripped
    ):
        report.inc("default_constraints_removed")
        return None

    # Keep only Fabric-compatible key constraints if any appear in future scripts.
    key_match = re.match(
        r"(?is)^(ALTER\s+TABLE\s+.+?\s+ADD\s+CONSTRAINT\s+\S+\s+"
        r"(?:PRIMARY\s+KEY|UNIQUE)\s+)(?:CLUSTERED|NONCLUSTERED)?\s*\((.*?)\).*$",
        stripped,
    )

    if key_match:
        report.inc("key_constraints_normalized")
        return (
            f"{key_match.group(1)}NONCLUSTERED "
            f"({clean_pk_columns(key_match.group(2))}) NOT ENFORCED;"
        )

    fk_match = re.match(
        r"(?is)^(ALTER\s+TABLE\s+.+?\s+ADD\s+CONSTRAINT\s+\S+\s+"
        r"FOREIGN\s+KEY\s*\(.*?\)\s+REFERENCES\s+.+?)(?:\s+.*)?$",
        stripped,
    )

    if fk_match:
        report.inc("foreign_keys_normalized")
        return f"{fk_match.group(1)} NOT ENFORCED;"

    if re.match(r"(?is)^ALTER\s+TABLE\b", stripped):
        report.inc("other_alter_table_removed")
        report.add(
            f"Removed unsupported or unrecognized ALTER TABLE statement: {stripped[:180]}"
        )
        return None

    return stripped + ";"


def convert_sqlserver_to_fabric(sql: str) -> tuple[str, ConversionReport]:
    report = ConversionReport()

    sql = strip_block_comments(sql)
    sql = sanitize_identifiers(sql, report)

    output_batches: list[str] = [
        "-- Converted for Microsoft Fabric Warehouse.",
        "-- Review the companion .report.txt before production execution.",
    ]

    deferred_constraints: list[str] = []

    for raw_batch in split_batches(sql):
        batch = remove_use_and_set_batches(raw_batch, report)
        if batch is None:
            continue

        stripped = batch.strip()
        if not stripped:
            continue

        if re.match(r"(?is)^CREATE\s+TABLE\b", stripped):
            create_sql, alters = rewrite_create_table(stripped, report)
            output_batches.append(create_sql)
            deferred_constraints.extend(alters)
            continue

        if re.match(r"(?is)^CREATE\s+SCHEMA\b", stripped):
            output_batches.append(rewrite_schema(stripped, report))
            continue

        if re.match(r"(?is)^ALTER\s+TABLE\b", stripped):
            alter = rewrite_alter_table(stripped, report)
            if alter:
                output_batches.append(alter)
            continue

        report.inc("other_batches_kept")
        output_batches.append(stripped.rstrip(";") + ";")

    if deferred_constraints:
        output_batches.append(
            "-- Primary key constraints converted from SQL Server inline syntax."
        )
        output_batches.extend(deferred_constraints)

    final_sql = "\n\n".join(output_batches).strip() + "\n"

    # Final cleanup after all CREATE TABLE statements are reconstructed.
    final_sql = remove_sparse_keyword(final_sql, report)

    return final_sql, report


def convert_file(input_path: str | Path, output_path: str | Path) -> None:
    input_path = Path(input_path)
    output_path = Path(output_path)

    sql = input_path.read_text(encoding="utf-8-sig", errors="replace")
    converted, report = convert_sqlserver_to_fabric(sql)

    output_path.write_text(converted, encoding="utf-8")
    report.write(output_path.with_suffix(output_path.suffix + ".report.txt"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert SQL Server DDL to Microsoft Fabric Warehouse DDL."
    )
    parser.add_argument("input", help="Input SQL Server DDL text file")
    parser.add_argument("output", help="Output Fabric Warehouse DDL text file")
    args = parser.parse_args()

    convert_file(args.input, args.output)

    print(f"Created: {args.output}")
    print(f"Report:  {args.output}.report.txt")


if __name__ == "__main__":
    main()
