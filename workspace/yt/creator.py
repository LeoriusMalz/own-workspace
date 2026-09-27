"""Schema validation and creation of tables, replicas, and directories."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from workspace.config import (
    MAX_SCHEMA_COLUMNS,
    MAX_SCHEMA_TEXT_BYTES,
    YT_CREATOR_COMPLEX_TYPES,
    YT_CREATOR_PRIMITIVE_TYPES,
    YT_CREATOR_TABLE_KINDS,
    YT_DIRECTORY_NODE_TYPES,
    YT_REPLICA_CLUSTERS,
)
from workspace.yt import common as yt_common
from workspace.yt.common import (
    YTCreationError,
    normalize_cluster,
    normalize_table_path,
    raise_normalized_yt_error,
    to_jsonable,
    yson_text,
)


def integer_setting(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} должен быть целым числом")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} должен быть целым числом") from error
    if result < minimum or result > maximum:
        raise ValueError(f"{name} должен быть от {minimum} до {maximum}")
    return result


def normalize_type_v3(value: Any) -> Any:
    value = to_jsonable(value)
    if isinstance(value, str):
        if value not in YT_CREATOR_PRIMITIVE_TYPES:
            raise ValueError(f"Неизвестный тип данных: {value}")
        aliases = {
            "boolean": "bool",
            "any": "yson",
        }
        return aliases.get(value, value)
    if not isinstance(value, dict):
        raise ValueError("type_v3 должен быть строкой или объектом")
    type_name = value.get("type_name")
    if type_name not in YT_CREATOR_COMPLEX_TYPES:
        raise ValueError(f"Неизвестный сложный тип данных: {type_name}")
    return value


def normalize_creator_schema(value: Any) -> list[dict[str, Any]]:
    value = to_jsonable(value)
    if not isinstance(value, list):
        raise ValueError("Схема должна быть списком колонок")
    if len(value) > MAX_SCHEMA_COLUMNS:
        raise ValueError(f"В схеме может быть не больше {MAX_SCHEMA_COLUMNS} колонок")

    names: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw_column in enumerate(value, start=1):
        if not isinstance(raw_column, dict):
            raise ValueError(f"Колонка {index} должна быть объектом")
        column = dict(raw_column)
        name = str(column.get("name") or "").strip()
        if not name:
            raise ValueError(f"У колонки {index} не указано имя")
        if name in names:
            raise ValueError(f"Имя колонки повторяется: {name}")
        names.add(name)
        column["name"] = name

        if "type_v3" in column:
            column["type_v3"] = normalize_type_v3(column["type_v3"])
        elif "type" in column:
            type_name = str(column["type"])
            if type_name not in YT_CREATOR_PRIMITIVE_TYPES:
                raise ValueError(f"Неизвестный тип данных: {type_name}")
            column["type"] = "boolean" if type_name == "bool" else type_name
            if "required" in column and not isinstance(column["required"], bool):
                raise ValueError(f"required у колонки {name} должен быть boolean")
        else:
            raise ValueError(f"У колонки {name} не указан type или type_v3")

        sort_order = column.get("sort_order")
        if sort_order in {None, "", "none"}:
            column.pop("sort_order", None)
        elif sort_order not in {"ascending", "descending"}:
            raise ValueError(f"Некорректное направление сортировки у колонки {name}")
        normalized.append(column)

    sorted_columns = [column for column in normalized if column.get("sort_order")]
    value_columns = [column for column in normalized if not column.get("sort_order")]
    return sorted_columns + value_columns


def unwrap_friendly_type(column: Mapping[str, Any]) -> tuple[str, bool] | None:
    supported_keys = {"name", "type", "required", "type_v3", "sort_order"}
    if set(column) - supported_keys:
        return None

    optional = False
    if "type_v3" in column:
        type_value = column["type_v3"]
        if isinstance(type_value, dict) and type_value.get("type_name") == "optional":
            optional = True
            type_value = type_value.get("item")
        if not isinstance(type_value, str) or type_value not in YT_CREATOR_PRIMITIVE_TYPES:
            return None
        type_name = "bool" if type_value == "boolean" else type_value
    else:
        type_name = str(column.get("type") or "")
        if type_name not in YT_CREATOR_PRIMITIVE_TYPES:
            return None
        type_name = "bool" if type_name == "boolean" else type_name
        optional = not bool(column.get("required", False))
    return type_name, optional


def friendly_schema_columns(schema: list[dict[str, Any]]) -> tuple[list[dict[str, Any]] | None, str | None]:
    result: list[dict[str, Any]] = []
    for column in schema:
        friendly_type = unwrap_friendly_type(column)
        if friendly_type is None:
            return None, (
                f"Колонка {column.get('name', 'без имени')} содержит сложный тип "
                "или настройки, которых нет в визуальном редакторе"
            )
        type_name, optional = friendly_type
        sort_order = column.get("sort_order")
        result.append({
            "name": column["name"],
            "type": type_name,
            "optional": optional,
            "sortOrder": (
                "asc" if sort_order == "ascending"
                else "desc" if sort_order == "descending"
                else "none"
            ),
        })
    return result, None


def parse_schema_text(text: Any, value_format: Any) -> list[dict[str, Any]]:
    if not isinstance(text, str):
        raise ValueError("Схема должна быть строкой")
    if len(text.encode("utf-8")) > MAX_SCHEMA_TEXT_BYTES:
        raise ValueError("Текст схемы слишком большой")
    if value_format == "json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"JSON: строка {error.lineno}, столбец {error.colno}: {error.msg}"
            ) from error
    elif value_format == "yson":
        try:
            import yt.yson as yson
            value = yson.loads(text.encode("utf-8"))
        except Exception as error:
            raise ValueError(f"YSON не удалось разобрать: {error}") from error
    else:
        raise ValueError("Формат должен быть json или yson")
    return normalize_creator_schema(value)


def schema_conversion_result(schema: list[dict[str, Any]]) -> dict[str, Any]:
    friendly, reason = friendly_schema_columns(schema)
    return {
        "schema": schema,
        "schemaJson": json.dumps(schema, ensure_ascii=False, indent=2),
        "schemaYson": yson_text(schema),
        "friendlyConvertible": friendly is not None,
        "friendlyReason": reason,
        "friendlyColumns": friendly,
    }


def validate_compression_codec(value: Any) -> str:
    codec = str(value or "zstd_5")
    if codec in {"none", "snappy", "lz4", "lz4_high_compression"}:
        return codec
    families = {
        "zlib": (1, 9),
        "zstd": (1, 21),
        "brotli": (1, 11),
        "lzma": (0, 9),
        "bzip2": (1, 9),
    }
    match = re.fullmatch(r"([a-z0-9]+)_([0-9]+)", codec)
    if not match or match.group(1) not in families:
        raise ValueError("Неизвестный compression codec")
    minimum, maximum = families[match.group(1)]
    level = int(match.group(2))
    if level < minimum or level > maximum:
        raise ValueError(f"Недопустимый уровень для {match.group(1)}")
    return codec


def normalize_creator_config(body: Mapping[str, Any], *, require_schema_rules: bool) -> dict[str, Any]:
    table_kind = str(body.get("tableKind") or "")
    if table_kind not in YT_CREATOR_TABLE_KINDS:
        raise ValueError("Выберите статический или динамический тип таблицы")
    replicated = bool(body.get("replicated"))
    if replicated and table_kind != "dynamic":
        raise ValueError("Реплицированной может быть только динамическая таблица")

    cluster = normalize_cluster("miranda" if replicated else body.get("cluster"))
    path = normalize_table_path(body.get("path"))
    schema = normalize_creator_schema(body.get("schema") or [])
    sorted_columns = [column for column in schema if column.get("sort_order")]
    if table_kind == "dynamic":
        if any(column.get("sort_order") == "descending" for column in schema):
            raise ValueError("Динамическая таблица поддерживает только ascending-сортировку")
        if require_schema_rules and not sorted_columns:
            raise ValueError("Для динамической таблицы нужна хотя бы одна сортированная колонка")

    bundle = str(body.get("bundle") or "vkvideo").strip()
    if table_kind == "dynamic" and not bundle:
        raise ValueError("Укажите tablet cell bundle")

    advanced_enabled = bool(body.get("advancedEnabled"))
    optimize_for = str(body.get("optimizeFor") or "scan")
    if optimize_for not in {"lookup", "scan"}:
        raise ValueError("Optimize for должен быть lookup или scan")
    compression_codec = validate_compression_codec(body.get("compressionCodec"))
    replication_factor = integer_setting(body.get("replicationFactor", 3), "Number of replicas", 1, 10)

    replica_targets: list[str] = []
    tracker_options: dict[str, Any] | None = None
    if replicated:
        raw_targets = body.get("replicaTargets")
        if not isinstance(raw_targets, list):
            raise ValueError("Выберите кластеры для реплик")
        replica_targets = list(dict.fromkeys(str(item) for item in raw_targets))
        if not replica_targets or set(replica_targets) - YT_REPLICA_CLUSTERS:
            raise ValueError("Реплики можно создать только на jupiter и saturn")

        min_sync = integer_setting(body.get("minSyncReplicaCount", 1), "min_sync_replica_count", 0, len(replica_targets))
        max_sync = integer_setting(body.get("maxSyncReplicaCount", 1), "max_sync_replica_count", 1, len(replica_targets))
        if min_sync > max_sync:
            raise ValueError("min_sync_replica_count не может быть больше max_sync_replica_count")

        preferred_raw = body.get("preferredSyncReplicaClusters") or []
        if not isinstance(preferred_raw, list):
            raise ValueError("preferred_sync_replica_clusters должен быть списком")
        preferred = list(dict.fromkeys(str(item) for item in preferred_raw))
        if set(preferred) - set(replica_targets):
            raise ValueError("Preferred-кластер должен входить в список создаваемых реплик")
        if len(preferred) > max_sync:
            raise ValueError("Число preferred-кластеров не может превышать max_sync_replica_count")
        tracker_options = {
            "enable_replicated_table_tracker": bool(body.get("enableReplicatedTableTracker", True)),
            "min_sync_replica_count": min_sync,
            "max_sync_replica_count": max_sync,
            "preferred_sync_replica_clusters": preferred,
        }

    return {
        "tableKind": table_kind,
        "replicated": replicated,
        "cluster": cluster,
        "replicaTargets": replica_targets,
        "path": path,
        "schema": schema,
        "bundle": bundle,
        "advancedEnabled": advanced_enabled,
        "optimizeFor": optimize_for,
        "compressionCodec": compression_codec,
        "replicationFactor": replication_factor,
        "trackerOptions": tracker_options,
        "leaveUnmounted": bool(body.get("leaveUnmounted", True)),
    }


def yt_parent_paths(path: str) -> list[str]:
    segments = path[2:].split("/")
    return ["//" + "/".join(segments[:index]) for index in range(1, len(segments))]


def yt_path_chain(path: str) -> list[str]:
    segments = path[2:].split("/")
    return ["//" + "/".join(segments[:index]) for index in range(1, len(segments) + 1)]


def destination_clusters(config: Mapping[str, Any]) -> list[str]:
    if config["replicated"]:
        return ["miranda", *config["replicaTargets"]]
    return [str(config["cluster"])]


def validate_creator_destinations(config: Mapping[str, Any], token: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for cluster in destination_clusters(config):
        client = yt_common.build_yt_client(token, cluster)
        path = str(config["path"])
        try:
            target_exists = bool(client.exists(path))
            missing_directories: list[str] = []
            non_directory_nodes: list[str] = []
            for parent in yt_parent_paths(path):
                if not client.exists(parent):
                    missing_directories.append(parent)
                    continue
                parent_type = str(client.get(f"{parent}/@type"))
                if parent_type not in YT_DIRECTORY_NODE_TYPES:
                    non_directory_nodes.append(parent)
        except Exception as error:
            raise_normalized_yt_error(error)

        checks.append({
            "cluster": cluster,
            "path": path,
            "targetExists": target_exists,
            "missingDirectories": missing_directories,
            "nonDirectoryNodes": non_directory_nodes,
            "ok": not target_exists and not missing_directories and not non_directory_nodes,
        })
    return {
        "valid": all(check["ok"] for check in checks),
        "checks": checks,
    }


def normalize_directory_config(body: Mapping[str, Any]) -> dict[str, Any]:
    cluster = normalize_cluster(body.get("cluster"))
    path = normalize_table_path(body.get("path"))
    annotation = str(body.get("annotation") or "").strip()
    if len(annotation) > 1000:
        raise ValueError("Описание директории не может быть длиннее 1000 символов")
    return {
        "cluster": cluster,
        "path": path,
        "inheritAcl": bool(body.get("inheritAcl", True)),
        "annotation": annotation,
    }


def inspect_directory_chain(config: Mapping[str, Any], token: str) -> dict[str, Any]:
    client = yt_common.build_yt_client(token, str(config["cluster"]))
    target_path = str(config["path"])
    steps: list[dict[str, Any]] = []
    missing_parent_seen = False
    blocked_by: dict[str, Any] | None = None

    try:
        for path in yt_path_chain(target_path):
            if missing_parent_seen:
                steps.append({
                    "path": path,
                    "status": "create",
                    "nodeType": None,
                })
                continue

            if not client.exists(path):
                missing_parent_seen = True
                steps.append({
                    "path": path,
                    "status": "create",
                    "nodeType": None,
                })
                continue

            node_type = str(client.get(f"{path}/@type"))
            is_directory = node_type in YT_DIRECTORY_NODE_TYPES
            is_target = path == target_path
            status = "exists" if is_directory and not is_target else "blocked"
            step = {
                "path": path,
                "status": status,
                "nodeType": node_type,
            }
            steps.append(step)
            if status == "blocked":
                blocked_by = step
                break
    except Exception as error:
        raise_normalized_yt_error(error)

    to_create = [step["path"] for step in steps if step["status"] == "create"]
    valid = blocked_by is None and bool(to_create) and to_create[-1] == target_path
    if blocked_by is not None:
        if blocked_by["path"] == target_path and blocked_by["nodeType"] in YT_DIRECTORY_NODE_TYPES:
            reason = "Целевая директория уже существует"
        else:
            reason = (
                f"Путь занят нодой типа {blocked_by['nodeType']}: "
                f"{blocked_by['path']}"
            )
    elif not to_create:
        reason = "Целевая директория уже существует"
    else:
        reason = None

    return {
        "valid": valid,
        "cluster": config["cluster"],
        "path": target_path,
        "steps": steps,
        "toCreate": to_create,
        "blockedBy": blocked_by,
        "reason": reason,
    }


def create_directory_chain(config: Mapping[str, Any], token: str, plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    client = yt_common.build_yt_client(token, str(config["cluster"]))
    created: list[dict[str, Any]] = []
    target_path = str(config["path"])
    try:
        for path in plan["toCreate"]:
            attributes: dict[str, Any] = {}
            if path == target_path:
                attributes["inherit_acl"] = config["inheritAcl"]
                if config["annotation"]:
                    attributes["annotation"] = config["annotation"]
            client.create("map_node", path, attributes=attributes)
            created.append({
                "cluster": config["cluster"],
                "path": path,
                "nodeType": "map_node",
            })
        return created
    except Exception as error:
        if created:
            raise YTCreationError(str(error), created) from error
        raise


def creator_table_attributes(config: Mapping[str, Any], *, upstream_replica_id: Any = None) -> dict[str, Any]:
    attributes: dict[str, Any] = {"schema": config["schema"]}
    if config["tableKind"] == "dynamic":
        attributes.update({
            "dynamic": True,
            "tablet_cell_bundle": config["bundle"],
        })
    if config["advancedEnabled"]:
        attributes.update({
            "optimize_for": config["optimizeFor"],
            "compression_codec": config["compressionCodec"],
            "replication_factor": config["replicationFactor"],
        })
    if upstream_replica_id is not None:
        attributes["upstream_replica_id"] = upstream_replica_id
    return attributes


def _create_yt_table(
    config: Mapping[str, Any],
    token: str,
    created: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    path = str(config["path"])
    if not config["replicated"]:
        client = yt_common.build_yt_client(token, str(config["cluster"]))
        client.create("table", path, attributes=creator_table_attributes(config))
        created.append({
            "cluster": config["cluster"],
            "path": path,
            "nodeType": "table",
            "tableType": config["tableKind"],
        })
        if config["tableKind"] == "dynamic" and not config["leaveUnmounted"]:
            client.mount_table(path, sync=True)
        return created

    meta_client = yt_common.build_yt_client(token, "miranda")
    logical_attributes = creator_table_attributes(config)
    logical_attributes["replicated_table_options"] = config["trackerOptions"]
    meta_client.create("replicated_table", path, attributes=logical_attributes)
    created.append({
        "cluster": "miranda",
        "path": path,
        "nodeType": "replicated_table",
        "tableType": "dynamic",
    })

    tracker_options = config["trackerOptions"] or {}
    preferred = list(tracker_options.get("preferred_sync_replica_clusters") or [])
    sync_clusters = list(preferred)
    for cluster in config["replicaTargets"]:
        if len(sync_clusters) >= tracker_options.get("min_sync_replica_count", 1):
            break
        if cluster not in sync_clusters:
            sync_clusters.append(cluster)

    for cluster in config["replicaTargets"]:
        replica_id = meta_client.create("table_replica", attributes={
            "table_path": path,
            "cluster_name": cluster,
            "replica_path": path,
            "mode": "sync" if cluster in sync_clusters else "async",
        })
        created.append({
            "cluster": "miranda",
            "path": f"#{replica_id}",
            "nodeType": "table_replica",
            "replicaCluster": cluster,
        })
        replica_client = yt_common.build_yt_client(token, cluster)
        replica_client.create(
            "table",
            path,
            attributes=creator_table_attributes(config, upstream_replica_id=replica_id),
        )
        created.append({
            "cluster": cluster,
            "path": path,
            "nodeType": "table",
            "tableType": "dynamic",
            "replicaId": str(replica_id),
        })
        meta_client.alter_table_replica(
            replica_id,
            enabled=True,
            enable_replicated_table_tracker=tracker_options.get("enable_replicated_table_tracker", True),
        )

    if not config["leaveUnmounted"]:
        meta_client.mount_table(path, sync=True)
        for cluster in config["replicaTargets"]:
            yt_common.build_yt_client(token, cluster).mount_table(path, sync=True)
    return created


def create_yt_table(config: Mapping[str, Any], token: str) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    try:
        return _create_yt_table(config, token, created)
    except Exception as error:
        if created:
            raise YTCreationError(str(error), created) from error
        raise
