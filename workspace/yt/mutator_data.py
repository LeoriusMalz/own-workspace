"""Typed row search, validation, insertion, update, and deletion."""

from __future__ import annotations

import json
import math
import uuid
from typing import Any, Mapping

from workspace.config import (
    YT_MUTATOR_INTEGER_RANGES,
    YT_MUTATOR_MAX_QUERY_LIMIT,
    YT_MUTATOR_MAX_WHERE_BYTES,
)
from workspace.yt import common as yt_common
from workspace.yt.common import YTManagerConflictError, to_jsonable, yson_text
from workspace.yt.creator import integer_setting
from workspace.yt.manager import normalize_tablet_state
from workspace.yt.mutator_schema import mutator_column_type


def mutator_cell_type(column: Mapping[str, Any]) -> tuple[str, bool, bool]:
    info = mutator_column_type(column)
    return str(info["baseType"]), bool(info["optional"]), bool(info["primitive"])


def mutator_parse_cell(cell: Any, column: Mapping[str, Any], *, field_name: str | None = None) -> Any:
    name = field_name or str(column.get("name") or "поле")
    if not isinstance(cell, dict):
        cell = {"value": cell, "isNull": cell is None}
    base_type, optional, primitive = mutator_cell_type(column)
    if bool(cell.get("isNull")):
        if not optional:
            raise ValueError(f"{name}: обязательная колонка не может быть null")
        return None

    raw = cell.get("value")
    text_value = "" if raw is None else str(raw)
    if text_value == "":
        if optional:
            return None
        raise ValueError(f"{name}: обязательное значение не заполнено")
    if not primitive:
        try:
            import yt.yson as yson
            return yson.loads(text_value.encode("utf-8"))
        except Exception as error:
            raise ValueError(f"{name}: сложное значение должно быть корректным YSON") from error

    if base_type in YT_MUTATOR_INTEGER_RANGES:
        try:
            value = int(text_value, 10)
        except ValueError as error:
            raise ValueError(f"{name}: ожидается целое число") from error
        minimum, maximum = YT_MUTATOR_INTEGER_RANGES[base_type]
        if value < minimum or value > maximum:
            raise ValueError(f"{name}: значение должно быть от {minimum} до {maximum}")
        if base_type.startswith("uint") or base_type in {"date", "datetime", "timestamp"}:
            import yt.yson as yson
            return yson.YsonUint64(value)
        return value
    if base_type in {"double", "float"}:
        try:
            value = float(text_value)
        except ValueError as error:
            raise ValueError(f"{name}: ожидается число") from error
        if not math.isfinite(value):
            raise ValueError(f"{name}: NaN и Infinity не поддерживаются")
        return value
    if base_type == "bool":
        normalized = text_value.strip().lower()
        if normalized not in {"true", "false"}:
            raise ValueError(f"{name}: допустимы только true или false")
        return normalized == "true"
    if base_type == "utf8":
        try:
            text_value.encode("utf-8", errors="strict")
        except UnicodeError as error:
            raise ValueError(f"{name}: строка должна быть корректным UTF-8") from error
        return text_value
    if base_type == "json":
        try:
            json.loads(text_value)
        except json.JSONDecodeError as error:
            raise ValueError(f"{name}: некорректный JSON — {error.msg}") from error
        return text_value
    if base_type == "uuid":
        try:
            return uuid.UUID(text_value).bytes
        except (ValueError, AttributeError) as error:
            raise ValueError(f"{name}: ожидается UUID") from error
    if base_type == "yson":
        try:
            import yt.yson as yson
            return yson.loads(text_value.encode("utf-8"))
        except Exception as error:
            raise ValueError(f"{name}: некорректный YSON") from error
    return text_value


def mutator_value_for_editor(value: Any, column: Mapping[str, Any]) -> dict[str, Any]:
    if value is None:
        return {"value": "", "isNull": True}
    base_type, _, primitive = mutator_cell_type(column)
    if base_type == "bool":
        text_value = "true" if bool(value) else "false"
    elif base_type == "uuid" and isinstance(value, (bytes, bytearray)) and len(value) == 16:
        text_value = str(uuid.UUID(bytes=bytes(value)))
    elif base_type in {"yson"} or not primitive:
        text_value = yson_text(value)
    elif base_type == "json" and not isinstance(value, str):
        text_value = json.dumps(to_jsonable(value), ensure_ascii=False)
    else:
        text_value = str(value)
    return {"value": text_value, "isNull": False}


def mutator_columns_by_name(info: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(column["name"]): dict(column["raw"]) for column in info["columns"]}


def mutator_identifier(name: str) -> str:
    return f"[{name}]"


def mutator_key_where(
    info: Mapping[str, Any], filters: Any,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(filters, list):
        raise ValueError("Фильтры ключей должны быть списком")
    key_columns = {column["name"]: column for column in info["keyColumns"]}
    schema_by_name = mutator_columns_by_name(info)
    expressions: list[str] = []
    placeholders: dict[str, Any] = {}
    placeholder_index = 0

    for raw_filter in filters:
        if not isinstance(raw_filter, dict) or not raw_filter.get("enabled"):
            continue
        name = str(raw_filter.get("name") or "")
        if name not in key_columns:
            raise ValueError(f"{name}: это не ключевая колонка")
        type_info = key_columns[name]
        operator = str(raw_filter.get("operator") or "=").upper()
        allowed = {"=", "IN"}
        if type_info["baseType"] not in {"bool", "yson", "json"} and type_info["primitive"]:
            allowed |= {">", ">=", "<", "<=", "BETWEEN"}
        if operator not in allowed:
            raise ValueError(f"{name}: операция {operator} недоступна для типа {type_info['displayType']}")
        raw_values = raw_filter.get("values")
        if not isinstance(raw_values, list):
            raw_values = [raw_filter.get("value")]
        if operator == "BETWEEN" and len(raw_values) != 2:
            raise ValueError(f"{name}: BETWEEN требует ровно два значения")
        if operator == "IN" and not raw_values:
            raise ValueError(f"{name}: IN требует хотя бы одно значение")
        if operator not in {"IN", "BETWEEN"}:
            raw_values = raw_values[:1]
        parsed_values = [
            mutator_parse_cell({"value": value, "isNull": value is None}, schema_by_name[name], field_name=name)
            for value in raw_values
        ]
        names: list[str] = []
        for value in parsed_values:
            placeholder_name = f"m{placeholder_index}"
            placeholder_index += 1
            placeholders[placeholder_name] = value
            names.append(f"{{{placeholder_name}}}")
        identifier = mutator_identifier(name)
        if operator == "IN":
            expression = f"{identifier} IN ({', '.join(names)})"
        elif operator == "BETWEEN":
            expression = f"{identifier} BETWEEN {names[0]} AND {names[1]}"
        else:
            expression = f"{identifier} {operator} {names[0]}"
        if bool(raw_filter.get("negated")):
            expression = f"NOT ({expression})"
        expressions.append(expression)
    return (" AND ".join(expressions) or "true"), placeholders


def mutator_custom_where(value: Any) -> str:
    where = str(value or "").strip()
    if not where:
        return "true"
    if len(where.encode("utf-8")) > YT_MUTATOR_MAX_WHERE_BYTES:
        raise ValueError("WHERE-фильтр слишком большой")
    if ";" in where:
        raise ValueError("В WHERE нельзя использовать точку с запятой")
    return where


def mutator_search_rows(info: Mapping[str, Any], token: str, body: Mapping[str, Any]) -> dict[str, Any]:
    if not info["capabilities"]["readRows"]:
        raise YTManagerConflictError(info["restrictions"]["data"])
    mode = str(body.get("mode") or "keys")
    if mode == "keys":
        if not info["keyColumns"]:
            raise ValueError("У таблицы нет ключевых колонок — используйте WHERE")
        where, placeholders = mutator_key_where(info, body.get("filters") or [])
    elif mode == "where":
        where = mutator_custom_where(body.get("where"))
        placeholders = {}
    else:
        raise ValueError("Неизвестный режим поиска")

    limit = integer_setting(body.get("limit", 10), "Лимит", 1, YT_MUTATOR_MAX_QUERY_LIMIT)
    apply_limit = bool(body.get("applyLimit", True))
    path = info["path"]
    client = yt_common.build_yt_client(token, info["cluster"])
    count_query = f"count(*) AS [count] FROM [{path}] WHERE ({where})"
    count_rows = list(client.select_rows(
        count_query,
        placeholder_values=placeholders,
        allow_full_scan=True,
    ))
    total = int((count_rows[0].get("count") if count_rows else 0) or 0)

    rows_query = f"* FROM [{path}] WHERE ({where})"
    if apply_limit:
        rows_query += f" LIMIT {limit}"
    iterator = client.select_rows(
        rows_query,
        placeholder_values=placeholders,
        allow_full_scan=True,
        output_row_limit=limit if apply_limit else None,
        fail_on_incomplete_result=False,
    )
    raw_rows: list[Mapping[str, Any]] = []
    try:
        for row in iterator:
            raw_rows.append(row)
            if len(raw_rows) >= limit:
                break
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()

    schema_by_name = mutator_columns_by_name(info)
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(raw_rows):
        values = {
            name: mutator_value_for_editor(row.get(name), column)
            for name, column in schema_by_name.items()
        }
        rows.append({"index": index + 1, "values": values})
    return {
        "rows": rows,
        "total": total,
        "shown": len(rows),
        "query": rows_query,
        "countQuery": count_query,
        "command": {
            "kind": "direct",
            "operationId": None,
            "operationUrl": None,
            "message": "select_rows выполняется напрямую и не создаёт Scheduler operation.",
        },
    }


def mutator_parse_row_cells(
    cells: Any,
    schema_by_name: Mapping[str, Mapping[str, Any]],
    *,
    require_all: bool,
    allowed_names: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(cells, dict):
        raise ValueError("Значения строки должны быть объектом")
    unknown = set(cells) - set(schema_by_name)
    if unknown:
        raise ValueError(f"Неизвестные колонки: {', '.join(sorted(unknown))}")
    if allowed_names is not None and set(cells) - allowed_names:
        raise ValueError("Нельзя менять ключевые или недоступные колонки")
    result: dict[str, Any] = {}
    for name, column in schema_by_name.items():
        if name in cells:
            result[name] = mutator_parse_cell(cells[name], column, field_name=name)
        elif require_all:
            _, optional, _ = mutator_cell_type(column)
            if optional:
                result[name] = None
            else:
                raise ValueError(f"{name}: обязательное значение не заполнено")
    return result


def mutator_key_signature(key: Mapping[str, Any]) -> str:
    return json.dumps(to_jsonable(key), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def mutator_existing_keys(
    client: Any, path: str, keys: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not keys:
        return []
    rows = list(client.lookup_rows(path, keys, keep_missing_rows=True))
    return [key for key, row in zip(keys, rows) if row is not None]


def mutator_validate_insert_rows(
    info: Mapping[str, Any], token: str, raw_rows: Any,
) -> dict[str, Any]:
    if not info["capabilities"]["writeRows"]:
        raise YTManagerConflictError(info["restrictions"]["data"])
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("Добавьте хотя бы одну запись")
    if len(raw_rows) > 500:
        raise ValueError("За один раз можно добавить не больше 500 записей")
    schema_by_name = mutator_columns_by_name(info)
    parsed_rows = [
        mutator_parse_row_cells(row, schema_by_name, require_all=True)
        for row in raw_rows
    ]
    key_names = [column["name"] for column in info["keyColumns"]]
    duplicate_indexes: list[int] = []
    existing_indexes: list[int] = []
    if key_names:
        seen: dict[str, int] = {}
        keys: list[dict[str, Any]] = []
        for index, row in enumerate(parsed_rows):
            key = {name: row[name] for name in key_names}
            signature = mutator_key_signature(key)
            if signature in seen:
                duplicate_indexes.append(index)
            else:
                seen[signature] = index
            keys.append(key)
        existing_signatures = {
            mutator_key_signature(key)
            for key in mutator_existing_keys(
                yt_common.build_yt_client(token, info["cluster"]), info["path"], keys,
            )
        }
        existing_indexes = [
            index for index, key in enumerate(keys)
            if mutator_key_signature(key) in existing_signatures
        ]
    return {
        "valid": not duplicate_indexes and not existing_indexes,
        "duplicateIndexes": duplicate_indexes,
        "existingIndexes": existing_indexes,
        "parsedRows": parsed_rows,
    }


def mutator_require_mounted(info: Mapping[str, Any]) -> None:
    state = normalize_tablet_state(info.get("tabletState"))
    if state != "mounted":
        raise YTManagerConflictError(
            f"Для изменения строк таблица должна быть mounted; текущее состояние: {state}"
        )


def mutator_apply_data_changes(
    info: Mapping[str, Any], token: str, body: Mapping[str, Any],
) -> dict[str, Any]:
    if not info["capabilities"]["writeRows"]:
        raise YTManagerConflictError(info["restrictions"]["data"])
    mutator_require_mounted(info)
    schema_by_name = mutator_columns_by_name(info)
    key_names = [column["name"] for column in info["keyColumns"]]
    if not key_names:
        raise YTManagerConflictError(
            "У таблицы нет ключевых колонок: найденные строки нельзя адресно изменить или удалить."
        )
    value_names = set(schema_by_name) - set(key_names)
    raw_deletes = body.get("deletes") or []
    raw_updates = body.get("updates") or []
    if not isinstance(raw_deletes, list) or not isinstance(raw_updates, list):
        raise ValueError("Некорректный пакет изменений")
    deletes: list[dict[str, Any]] = []
    for raw_key in raw_deletes:
        key = mutator_parse_row_cells(
            raw_key, {name: schema_by_name[name] for name in key_names}, require_all=True,
        )
        deletes.append(key)
    updates: list[dict[str, Any]] = []
    for raw_update in raw_updates:
        if not isinstance(raw_update, dict):
            raise ValueError("Некорректное обновление строки")
        key = mutator_parse_row_cells(
            raw_update.get("keys"),
            {name: schema_by_name[name] for name in key_names},
            require_all=True,
        )
        changes = mutator_parse_row_cells(
            raw_update.get("changes"), schema_by_name, require_all=False, allowed_names=value_names,
        )
        if changes:
            updates.append({**key, **changes})
    if not deletes and not updates:
        raise ValueError("Нет изменений для применения")

    client = yt_common.build_yt_client(token, info["cluster"])
    with client.Transaction(type="tablet"):
        if deletes:
            client.delete_rows(info["path"], deletes)
        if updates:
            client.insert_rows(info["path"], updates, update=True)
    return {
        "deleted": len(deletes),
        "updated": len(updates),
        "command": {
            "kind": "direct",
            "operationId": None,
            "operationUrl": None,
            "message": "Tablet transaction выполняется напрямую и не создаёт Scheduler operation.",
        },
    }


def mutator_insert_rows(
    info: Mapping[str, Any], token: str, raw_rows: Any,
) -> dict[str, Any]:
    mutator_require_mounted(info)
    validation = mutator_validate_insert_rows(info, token, raw_rows)
    if not validation["valid"]:
        raise YTManagerConflictError(
            "Ключи изменились: есть дубликаты в пакете или записи с такими ключами уже появились в таблице."
        )
    client = yt_common.build_yt_client(token, info["cluster"])
    client.insert_rows(info["path"], validation["parsedRows"], update=False)
    return {
        "inserted": len(validation["parsedRows"]),
        "command": {
            "kind": "direct",
            "operationId": None,
            "operationUrl": None,
            "message": "insert_rows выполняется напрямую и не создаёт Scheduler operation.",
        },
    }
