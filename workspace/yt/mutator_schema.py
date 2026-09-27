"""Schema inspection, change planning, and safe schema application."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from workspace.config import (
    YT_MUTATOR_SIGNED_INTEGER_TYPES,
    YT_MUTATOR_SUPPORTED_TYPES,
    YT_MUTATOR_UNSIGNED_INTEGER_TYPES,
    YT_TABLE_NODE_TYPES,
)
from workspace.yt import common as yt_common
from workspace.yt.common import (
    YTManagerConflictError,
    YTManagerPartialError,
    normalize_cluster,
    normalize_table_path,
    optional_attribute,
    required_attribute,
    to_jsonable,
    yson_text,
)
from workspace.yt.creator import normalize_creator_schema
from workspace.yt.manager import manager_inspect, normalize_tablet_state


def mutator_schema_attributes(schema_raw: Any) -> dict[str, Any]:
    attributes = to_jsonable(getattr(schema_raw, "attributes", {}) or {})
    if not isinstance(attributes, dict):
        attributes = {}
    return {
        **attributes,
        "strict": bool(attributes.get("strict", True)),
        "unique_keys": bool(attributes.get("unique_keys", False)),
    }


def mutator_column_type(column: Mapping[str, Any]) -> dict[str, Any]:
    type_v3 = to_jsonable(column.get("type_v3"))
    optional = False
    base_type: Any = type_v3
    if isinstance(type_v3, dict) and type_v3.get("type_name") == "optional":
        optional = True
        base_type = type_v3.get("item")
    elif type_v3 is None:
        legacy_type = str(column.get("type") or "")
        optional = not bool(column.get("required", False))
        base_type = {"boolean": "bool", "any": "yson"}.get(legacy_type, legacy_type)

    primitive = isinstance(base_type, str)
    base_name = str(base_type) if primitive else str((base_type or {}).get("type_name") or "complex")
    base_name = {"boolean": "bool", "any": "yson"}.get(base_name, base_name)
    if base_name == "yson":
        optional = True
    return {
        "baseType": base_name,
        "optional": optional,
        "primitive": primitive and base_name in YT_MUTATOR_SUPPORTED_TYPES,
        "typeV3": type_v3,
        "displayType": base_name if primitive else json.dumps(base_type, ensure_ascii=False),
    }


def mutator_schema_columns(schema: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, column in enumerate(schema):
        type_info = mutator_column_type(column)
        result.append({
            "index": index,
            "name": str(column.get("name") or ""),
            "key": column.get("sort_order") is not None,
            "sortOrder": column.get("sort_order"),
            "computed": column.get("expression") is not None,
            "expression": column.get("expression"),
            "aggregate": column.get("aggregate"),
            **type_info,
            "raw": to_jsonable(column),
        })
    return result


def mutator_compatible_target_types(source_type: str) -> list[str]:
    """Return conservative, lossless primitive type transitions.

    Integer types may only be widened while preserving signedness. Other
    primitive types require an exact match; conversions that need reading and
    rewriting table data belong in a Map/Merge migration, not alter_table.
    """
    for family in (YT_MUTATOR_SIGNED_INTEGER_TYPES, YT_MUTATOR_UNSIGNED_INTEGER_TYPES):
        if source_type in family:
            return list(family[family.index(source_type):])
    return [source_type]


def mutator_schema_policy(
    columns: list[dict[str, Any]], *, strict: bool, dynamic: bool, row_count: int,
) -> dict[str, Any]:
    if dynamic:
        strict_control = {
            "canToggle": False,
            "reason": "У динамической таблицы schema должна оставаться strict.",
        }
    elif strict:
        strict_control = {
            "canToggle": True,
            "reason": "Отключение strict разрешено; неизвестные поля больше не будут запрещены схемой.",
        }
    elif row_count == 0:
        strict_control = {
            "canToggle": True,
            "reason": "Пустую статическую таблицу можно перевести обратно в strict.",
        }
    else:
        strict_control = {
            "canToggle": False,
            "reason": "Непустую non-strict таблицу нельзя сделать strict без чтения и проверки всех данных.",
        }

    enriched_columns: list[dict[str, Any]] = []
    for column in columns:
        protected = bool(column["key"] or column["computed"] or column["aggregate"])
        base_compatible_types = mutator_compatible_target_types(str(column["baseType"]))
        can_change = bool(
            column["primitive"]
            and not protected
            and not (row_count > 0 and column["optional"])
            and not (column["optional"] and len(base_compatible_types) == 1)
        )
        compatible_types = base_compatible_types if can_change else [column["baseType"]]
        if row_count > 0:
            compatible_types = [column["baseType"]]

        can_delete = bool(not strict and not protected)
        if strict:
            delete_reason = "В strict-схеме удаление колонок отключено. Сначала осознанно выключите strict."
        elif column["key"]:
            delete_reason = "Ключевую колонку нельзя удалить этим инструментом."
        elif column["computed"] or column["aggregate"]:
            delete_reason = "Computed/aggregate колонка требует отдельной миграции схемы."
        else:
            delete_reason = "Колонку можно удалить из non-strict схемы."

        if protected:
            change_reason = "Ключевые, computed и aggregate колонки не меняются этим инструментом."
        elif not column["primitive"]:
            change_reason = "Сложный type_v3 нужно менять через отдельную миграцию данных."
        elif row_count > 0 and column["optional"]:
            change_reason = "Колонка уже optional, а базовый тип непустой таблицы менять нельзя."
        elif row_count > 0:
            change_reason = "В непустой таблице базовый тип менять нельзя; доступно только required → optional."
        else:
            change_reason = "Допустимо только безопасное расширение целого типа с той же знаковостью."

        enriched = dict(column)
        enriched["policy"] = {
            "canDelete": can_delete,
            "deleteReason": delete_reason,
            "canChange": can_change,
            "changeReason": change_reason,
            "allowedTargetTypes": compatible_types,
            "canEnableOptional": bool(can_change and not column["optional"]),
            "canDisableOptional": False,
        }
        enriched_columns.append(enriched)

    return {
        "strict": {"value": strict, **strict_control},
        "columns": enriched_columns,
    }


def mutator_schema_hash(schema: Any, attributes: Any) -> str:
    payload = json.dumps(
        {"schema": to_jsonable(schema), "attributes": to_jsonable(attributes)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mutator_table_info(path: str, token: str, cluster: str) -> dict[str, Any]:
    manager_info = manager_inspect(path, token, cluster)
    if manager_info["kind"] == "link":
        raise ValueError("YT Mutator работает с самой таблицей, а не с символической ссылкой")
    if manager_info["nodeType"] not in YT_TABLE_NODE_TYPES:
        raise ValueError("YT Mutator поддерживает только таблицы")

    client = yt_common.build_yt_client(token, cluster)
    schema_raw = required_attribute(client, path, "schema") or []
    schema = to_jsonable(schema_raw)
    if not isinstance(schema, list):
        schema = []
    schema_attributes = mutator_schema_attributes(schema_raw)
    columns = mutator_schema_columns(schema)
    row_count = optional_attribute(client, path, "row_count")
    try:
        row_count_number = int(row_count or 0)
    except (TypeError, ValueError):
        row_count_number = 0

    schema_policy = mutator_schema_policy(
        columns,
        strict=bool(schema_attributes.get("strict", True)),
        dynamic=bool(manager_info.get("dynamic")),
        row_count=row_count_number,
    )
    columns = schema_policy["columns"]

    physical_replica = manager_info["kind"] == "physical_replica"
    dynamic = bool(manager_info.get("dynamic"))
    tablet_state = manager_info.get("tabletState")
    read_reason = None
    write_reason = None
    if not dynamic:
        read_reason = "Статические таблицы нельзя изменять построчно без перезаписи chunks."
        write_reason = read_reason
    elif physical_replica:
        read_reason = "Физическую реплику нельзя менять напрямую — выберите logical replicated_table в Miranda."
        write_reason = read_reason
    elif normalize_tablet_state(tablet_state) not in {"mounted", "frozen"}:
        read_reason = (
            "Для select_rows таблица должна быть mounted или frozen. "
            f"Текущее состояние: {normalize_tablet_state(tablet_state)}."
        )
        write_reason = read_reason
    elif normalize_tablet_state(tablet_state) != "mounted":
        write_reason = "Frozen-таблица доступна только для чтения; для записи сначала выполните unfreeze/mount."

    alter_reason = None
    if physical_replica:
        alter_reason = "Схему физической реплики нужно менять через logical replicated_table в Miranda."

    return {
        "cluster": cluster,
        "path": path,
        "nodeType": manager_info["nodeType"],
        "kind": manager_info["kind"],
        "kindLabel": manager_info["kindLabel"],
        "dynamic": dynamic,
        "replicated": manager_info["kind"] == "replicated_table",
        "physicalReplica": physical_replica,
        "tabletState": tablet_state,
        "optimizeFor": optional_attribute(client, path, "optimize_for"),
        "schemaMode": optional_attribute(client, path, "schema_mode"),
        "rowCount": row_count_number,
        "schema": schema,
        "schemaYson": yson_text(schema_raw),
        "schemaAttributes": schema_attributes,
        "schemaHash": mutator_schema_hash(schema, schema_attributes),
        "columns": columns,
        "keyColumns": [column for column in columns if column["key"]],
        "valueColumns": [column for column in columns if not column["key"]],
        "replication": manager_info.get("replication"),
        "schemaPolicy": schema_policy,
        "capabilities": {
            "alterSchema": not physical_replica,
            "readRows": read_reason is None,
            "writeRows": write_reason is None,
        },
        "restrictions": {
            "alterSchema": alter_reason,
            "data": read_reason,
            "readRows": read_reason,
            "writeRows": write_reason,
        },
    }


def mutator_validate_column_name(name: Any, existing_names: set[str], current_name: str | None = None) -> str:
    value = str(name or "").strip()
    if not value:
        raise ValueError("Введите имя колонки")
    if len(value) > 256:
        raise ValueError("Имя колонки не может быть длиннее 256 символов")
    if value.startswith("@"):
        raise ValueError("Имя колонки не может начинаться с системного префикса @")
    if value != current_name and value in existing_names:
        raise ValueError(f"Колонка {value} уже существует")
    return value


def mutator_new_column(name: str, base_type: Any, optional: Any) -> dict[str, Any]:
    type_name = str(base_type or "")
    if type_name not in YT_MUTATOR_SUPPORTED_TYPES:
        raise ValueError("Для визуального изменения выберите поддерживаемый примитивный тип")
    is_optional = bool(optional)
    if type_name == "yson" and not is_optional:
        raise ValueError("Колонка yson/any не может быть обязательной")
    legacy_type = "boolean" if type_name == "bool" else "any" if type_name == "yson" else type_name
    type_v3: Any = {"type_name": "optional", "item": type_name} if is_optional else type_name
    return {
        "name": name,
        "type": legacy_type,
        "required": not is_optional,
        "type_v3": type_v3,
    }


def mutator_replica_schema_preflight(info: Mapping[str, Any], token: str) -> dict[str, Any]:
    logical_hash = mutator_schema_hash(info["schema"], info["schemaAttributes"])
    mismatches: list[dict[str, str]] = []
    row_counts = [int(info.get("rowCount") or 0)]
    replicas = (info.get("replication") or {}).get("replicas") or []
    for replica in replicas:
        cluster = normalize_cluster(replica["cluster"])
        path = normalize_table_path(replica["path"])
        client = yt_common.build_yt_client(token, cluster)
        schema_raw = required_attribute(client, path, "schema") or []
        schema = to_jsonable(schema_raw)
        schema_attributes = mutator_schema_attributes(schema_raw)
        if mutator_schema_hash(schema, schema_attributes) != logical_hash:
            mismatches.append({"cluster": cluster, "path": path})
        row_count = optional_attribute(client, path, "row_count")
        try:
            row_counts.append(int(row_count or 0))
        except (TypeError, ValueError):
            row_counts.append(0)
    return {
        "replicaCount": len(replicas),
        "maxRowCount": max(row_counts),
        "mismatches": mismatches,
    }


def mutator_schema_plan(
    info: Mapping[str, Any], change: Any, token: str | None = None,
) -> dict[str, Any]:
    if not info["capabilities"]["alterSchema"]:
        raise YTManagerConflictError(info["restrictions"]["alterSchema"])
    if not isinstance(change, dict):
        raise ValueError("Описание изменения схемы должно быть объектом")
    action = str(change.get("action") or "")
    schema = [dict(column) for column in info["schema"]]
    existing_names = {str(column.get("name") or "") for column in schema}
    schema_attributes = dict(info["schemaAttributes"])
    strict = bool(schema_attributes.get("strict", True))
    effective_row_count = int(info.get("rowCount") or 0)
    replica_preflight = {"replicaCount": 0, "maxRowCount": effective_row_count, "mismatches": []}
    if info.get("replicated") and token is not None:
        replica_preflight = mutator_replica_schema_preflight(info, token)
        if replica_preflight["mismatches"]:
            paths = ", ".join(
                f"{item['cluster']}:{item['path']}" for item in replica_preflight["mismatches"]
            )
            raise YTManagerConflictError(
                "Схема logical replicated_table расходится со схемой физических реплик: " + paths
            )
        effective_row_count = int(replica_preflight["maxRowCount"])
    non_empty = effective_row_count > 0
    warnings: list[str] = []
    steps: list[str] = []

    if non_empty and str(info.get("schemaMode") or "").lower() != "strong":
        raise YTManagerConflictError(
            "У непустой таблицы weak schema. Сначала задайте и проверьте strong schema через отдельную миграцию данных."
        )

    if action == "set_strict":
        requested_strict = change.get("strict")
        if not isinstance(requested_strict, bool):
            raise ValueError("Новое значение strict должно быть boolean")
        if requested_strict == strict:
            raise YTManagerConflictError("Strict уже находится в выбранном состоянии")
        if info.get("dynamic"):
            raise YTManagerConflictError("У динамической и реплицированной таблицы schema должна оставаться strict")
        if requested_strict and non_empty:
            raise YTManagerConflictError(
                "Непустую non-strict таблицу нельзя сделать strict без чтения и проверки всех строк. "
                "Выполните отдельную миграцию через Merge или Map."
            )
        schema_attributes["strict"] = requested_strict
        steps.append(f"Установить strict = {str(requested_strict).lower()}")
        if not requested_strict:
            warnings.append("После отключения strict строки смогут содержать поля, отсутствующие в schema.")
    elif action == "add":
        name = mutator_validate_column_name(change.get("name"), existing_names)
        column = mutator_new_column(name, change.get("baseType"), change.get("optional"))
        if non_empty and not strict:
            raise YTManagerConflictError(
                "В непустую non-strict таблицу нельзя безопасно добавить типизированную колонку: "
                "в старых строках уже может существовать одноимённое значение другого типа."
            )
        if non_empty and column["required"]:
            raise YTManagerConflictError(
                "В непустую таблицу можно добавить только optional-колонку: существующие строки получат null."
            )
        schema.append(column)
        steps.append(f"Добавить колонку {name}")
        if non_empty:
            warnings.append("Для существующих строк новая колонка будет иметь значение null.")
    elif action == "delete":
        name = str(change.get("name") or "")
        index = next((i for i, column in enumerate(schema) if column.get("name") == name), None)
        if index is None:
            raise ValueError("Колонка больше не существует")
        if schema[index].get("sort_order") is not None:
            raise YTManagerConflictError("Удаление ключевой колонки через YT Mutator запрещено")
        if strict:
            raise YTManagerConflictError(
                "Из strict-схемы нельзя удалить колонку этим инструментом. "
                "Сначала осознанно отключите strict либо выполните отдельную миграцию через Merge или Map."
            )
        if schema[index].get("expression") is not None or schema[index].get("aggregate") is not None:
            raise YTManagerConflictError("Computed/aggregate колонку нельзя удалить этим инструментом")
        schema.pop(index)
        steps.append(f"Удалить колонку {name} из схемы")
        if non_empty:
            warnings.append(
                "Таблица non-strict: alter_table уберёт колонку из схемы, но не перепишет старые chunks."
            )
    elif action == "change_type":
        name = str(change.get("name") or "")
        index = next((i for i, column in enumerate(schema) if column.get("name") == name), None)
        if index is None:
            raise ValueError("Колонка больше не существует")
        old_column = schema[index]
        old_type = mutator_column_type(old_column)
        replacement = mutator_new_column(name, change.get("baseType"), change.get("optional"))
        new_type = mutator_column_type(replacement)
        if old_column.get("sort_order") is not None:
            raise YTManagerConflictError("Тип и optional ключевой колонки нельзя менять этим инструментом")
        if old_column.get("expression") is not None or old_column.get("aggregate") is not None:
            raise YTManagerConflictError("Тип computed/aggregate колонки нельзя менять этим инструментом")
        if not old_type["primitive"]:
            raise YTManagerConflictError("Сложный type_v3 нельзя менять визуальным редактором")
        if old_type["optional"] and not new_type["optional"]:
            raise YTManagerConflictError("Optional можно включить, но нельзя выключить без проверки и перезаписи данных")
        if new_type["baseType"] not in mutator_compatible_target_types(str(old_type["baseType"])):
            raise YTManagerConflictError(
                f"Небезопасное преобразование {old_type['baseType']} → {new_type['baseType']}. "
                "Разрешено только расширение целого типа с той же знаковостью."
            )
        if non_empty and new_type["baseType"] != old_type["baseType"]:
            raise YTManagerConflictError(
                "Базовый тип непустой таблицы нельзя менять через alter_table: нужна полная "
                "перезапись данных через Merge или Map."
            )
        if (
            new_type["baseType"] == old_type["baseType"]
            and new_type["optional"] == old_type["optional"]
        ):
            raise YTManagerConflictError("Тип и optional не изменились")
        for preserved in ("sort_order", "expression", "aggregate", "lock", "group"):
            if preserved in old_column:
                replacement[preserved] = old_column[preserved]
        schema[index] = replacement
        if new_type["baseType"] != old_type["baseType"]:
            steps.append(f"Расширить тип колонки {name}: {old_type['baseType']} → {new_type['baseType']}")
        if new_type["optional"] != old_type["optional"]:
            steps.append(f"Сделать колонку {name} optional")
    else:
        raise ValueError("Неизвестное изменение схемы")

    normalized = normalize_creator_schema(schema)
    return {
        "allowed": True,
        "action": action,
        "newSchema": normalized,
        "schemaAttributes": schema_attributes,
        "steps": steps,
        "warnings": warnings,
        "requiresUnmount": bool(info["dynamic"]),
        "replicaCount": int(replica_preflight["replicaCount"]),
        "effectiveRowCount": effective_row_count,
    }


def mutator_yson_schema(schema: list[dict[str, Any]], attributes: Mapping[str, Any]) -> Any:
    import yt.yson as yson

    return yson.to_yson_type(schema, attributes=dict(attributes))


def mutator_schema_targets(info: Mapping[str, Any], token: str) -> list[dict[str, Any]]:
    targets = [{
        "cluster": info["cluster"],
        "path": info["path"],
        "logical": info["kind"] == "replicated_table",
    }]
    if info["kind"] == "replicated_table":
        for replica in (info.get("replication") or {}).get("replicas") or []:
            targets.append({
                "cluster": normalize_cluster(replica["cluster"]),
                "path": normalize_table_path(replica["path"]),
                "logical": False,
            })
    for target in targets:
        target_client = yt_common.build_yt_client(token, target["cluster"])
        target["tabletState"] = normalize_tablet_state(
            optional_attribute(target_client, target["path"], "tablet_state")
        ) if info["dynamic"] else None
    return targets


def mutator_apply_schema(
    info: Mapping[str, Any], plan: Mapping[str, Any], token: str,
) -> dict[str, Any]:
    schema_value = mutator_yson_schema(plan["newSchema"], plan["schemaAttributes"])
    targets = mutator_schema_targets(info, token)
    completed: list[dict[str, Any]] = []
    unmounted: list[dict[str, Any]] = []
    action_error: Exception | None = None
    restore_errors: list[str] = []
    try:
        if info["dynamic"]:
            for target in reversed(targets):
                if target["tabletState"] != "unmounted":
                    yt_common.build_yt_client(token, target["cluster"]).unmount_table(target["path"], sync=True)
                    unmounted.append(target)
                    completed.append({"action": "unmount", "cluster": target["cluster"], "path": target["path"]})

        for target in targets:
            yt_common.build_yt_client(token, target["cluster"]).alter_table(target["path"], schema=schema_value)
            completed.append({"action": "alter_table", "cluster": target["cluster"], "path": target["path"]})
    except Exception as error:
        action_error = error
    finally:
        # A failed alter must not leave tables unexpectedly unmounted. Restore
        # every target whose original state we changed, including replicas.
        for target in unmounted:
            try:
                yt_common.build_yt_client(token, target["cluster"]).mount_table(
                    target["path"], sync=True, freeze=target["tabletState"] == "frozen",
                )
                completed.append({"action": "mount", "cluster": target["cluster"], "path": target["path"]})
            except Exception as error:
                restore_errors.append(f"{target['cluster']}:{target['path']}: {error}")

    if action_error is not None or restore_errors:
        messages = []
        if action_error is not None:
            messages.append(str(action_error))
        if restore_errors:
            messages.append("не удалось восстановить состояние: " + "; ".join(restore_errors))
        error = RuntimeError("; ".join(messages))
        if completed:
            raise YTManagerPartialError(str(error), completed) from error
        raise error

    return {
        "completed": completed,
        "command": {
            "kind": "direct",
            "operationId": None,
            "operationUrl": None,
            "message": "alter_table выполняется напрямую и не создаёт Scheduler operation.",
        },
    }
