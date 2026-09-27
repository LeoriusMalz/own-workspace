"""Node operations, storage attributes, mounting, and replica management."""

from __future__ import annotations

from typing import Any, Mapping

from workspace.common import LOGGER
from workspace.config import (
    MAX_MANAGER_DELETE_PREVIEW,
    YT_DIRECTORY_NODE_TYPES,
    YT_MANAGER_MUTABLE_ATTRIBUTES,
    YT_TABLE_NODE_TYPES,
)
from workspace.yt import common as yt_common
from workspace.yt.common import (
    YTAccessDeniedError,
    YTManagerConflictError,
    YTManagerPartialError,
    is_yt_access_denied,
    normalize_cluster,
    normalize_table_path,
    optional_attribute,
    raise_normalized_yt_error,
    required_attribute,
    to_jsonable,
)
from workspace.yt.creator import integer_setting, validate_compression_codec, yt_parent_paths


def manager_raw_path(path: str) -> str:
    return f"{path}&"


def manager_node_exists(client: Any, path: str) -> bool:
    """Check the Cypress node itself, including a broken symbolic link."""
    try:
        if client.exists(manager_raw_path(path)):
            return True
    except Exception as error:
        if is_yt_access_denied(error):
            raise YTAccessDeniedError("Доступ ограничен для данного токена") from error
        LOGGER.debug("Raw YT path check is unavailable: %s", path, exc_info=True)
    try:
        return bool(client.exists(path))
    except Exception as error:
        raise_normalized_yt_error(error)
    return False


def manager_raw_attribute(client: Any, path: str, name: str) -> Any:
    raw_path = manager_raw_path(path)
    try:
        if client.exists(f"{raw_path}/@{name}"):
            return client.get(f"{raw_path}/@{name}")
    except Exception as error:
        if is_yt_access_denied(error):
            raise YTAccessDeniedError("Доступ ограничен для данного токена") from error
        LOGGER.debug("Raw YT attribute is unavailable: %s/@%s", raw_path, name, exc_info=True)
    return optional_attribute(client, path, name)


def normalize_replica_id(value: Any) -> str | None:
    if value in {None, "", "0-0-0-0"}:
        return None
    return str(value)


def normalize_tablet_state(value: Any) -> str:
    return str(value or "unmounted").lower()


def manager_replica_details(
    meta_client: Any,
    replica_id: str,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = fallback or {}
    object_path = f"#{replica_id}"

    def replica_attribute(name: str, *fallback_names: str) -> Any:
        value = optional_attribute(meta_client, object_path, name)
        if value is not None:
            return value
        for fallback_name in fallback_names:
            if fallback_name in fallback:
                return fallback[fallback_name]
        return None

    tracker_enabled = replica_attribute(
        "enable_replicated_table_tracker",
        "enable_replicated_table_tracker",
        "replicated_table_tracker_enabled",
    )
    if tracker_enabled is None:
        tracker_enabled = replica_attribute(
            "replicated_table_tracker_enabled",
            "replicated_table_tracker_enabled",
        )
    return {
        "id": replica_id,
        "cluster": str(replica_attribute("cluster_name", "cluster_name", "cluster") or ""),
        "path": str(replica_attribute("replica_path", "replica_path", "path") or ""),
        "tablePath": str(replica_attribute("table_path", "table_path") or ""),
        "state": str(replica_attribute("state", "state") or "disabled"),
        "mode": str(replica_attribute("mode", "mode") or "async"),
        "trackerEnabled": bool(tracker_enabled) if tracker_enabled is not None else True,
    }


def manager_replicas(meta_client: Any, table_path: str, replicas_value: Any) -> list[dict[str, Any]]:
    replicas_json = to_jsonable(replicas_value)
    if not isinstance(replicas_json, dict):
        return []
    result: list[dict[str, Any]] = []
    for raw_id, raw_attributes in replicas_json.items():
        replica_id = str(raw_id)
        fallback = raw_attributes if isinstance(raw_attributes, dict) else {}
        details = manager_replica_details(meta_client, replica_id, fallback)
        if not details["tablePath"]:
            details["tablePath"] = table_path
        result.append(details)
    result.sort(key=lambda item: (item["cluster"], item["path"], item["id"]))
    return result


def manager_directory_preview(client: Any, path: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    truncated = False
    try:
        for index, item in enumerate(client.search(path, attributes=["type"], follow_links=False)):
            item_path = str(item)
            if item_path == path:
                continue
            if len(items) >= MAX_MANAGER_DELETE_PREVIEW:
                truncated = True
                break
            attributes = getattr(item, "attributes", {}) or {}
            items.append({
                "path": item_path,
                "nodeType": str(attributes.get("type") or "node"),
            })
    except Exception as error:
        if is_yt_access_denied(error):
            raise YTAccessDeniedError("Доступ ограничен для данного токена") from error
        LOGGER.debug("YT directory preview failed: %s", path, exc_info=True)
    return {"items": items, "truncated": truncated, "countShown": len(items)}


def manager_kind_label(kind: str) -> str:
    return {
        "directory": "Директория",
        "static_table": "Статическая таблица",
        "dynamic_table": "Динамическая таблица",
        "replicated_table": "Реплицированная таблица",
        "physical_replica": "Физическая таблица-реплика",
        "link": "Ссылка",
        "node": "YT-нода",
    }.get(kind, kind)


def manager_inspect(path: str, token: str, cluster: str) -> dict[str, Any]:
    client = yt_common.build_yt_client(token, cluster)
    if not manager_node_exists(client, path):
        raise FileNotFoundError(path)

    raw_type = str(manager_raw_attribute(client, path, "type") or "")
    if raw_type == "link":
        target_path = manager_raw_attribute(client, path, "target_path")
        return {
            "cluster": cluster,
            "proxy": f"{cluster}.yt.vk.team",
            "path": path,
            "nodeType": "link",
            "kind": "link",
            "kindLabel": manager_kind_label("link"),
            "link": {
                "targetPath": str(target_path or ""),
                "broken": bool(manager_raw_attribute(client, path, "broken")),
            },
            "capabilities": {
                "editAttributes": False,
                "createLink": False,
                "copy": False,
                "move": False,
                "mount": False,
                "replication": False,
                "delete": True,
            },
            "deletePlan": {
                "kind": "link",
                "items": [{"cluster": cluster, "path": path, "action": "Удалить ссылку"}],
            },
        }

    node_type = str(required_attribute(client, path, "type"))
    is_directory = node_type in YT_DIRECTORY_NODE_TYPES
    is_table = node_type in YT_TABLE_NODE_TYPES
    dynamic = bool(optional_attribute(client, path, "dynamic")) if is_table else False
    upstream_replica_id = (
        normalize_replica_id(optional_attribute(client, path, "upstream_replica_id"))
        if is_table else None
    )
    is_logical_replicated = node_type in {"replicated_table", "chaos_replicated_table"}

    if is_directory:
        kind = "directory"
    elif is_logical_replicated:
        kind = "replicated_table"
    elif upstream_replica_id:
        kind = "physical_replica"
    elif is_table and dynamic:
        kind = "dynamic_table"
    elif is_table:
        kind = "static_table"
    else:
        kind = "node"

    initial_attributes = {
        "primaryMedium": optional_attribute(client, path, "primary_medium"),
        "tabletCellBundle": optional_attribute(client, path, "tablet_cell_bundle"),
        "optimizeFor": optional_attribute(client, path, "optimize_for"),
        "compressionCodec": optional_attribute(client, path, "compression_codec"),
        "replicationFactor": optional_attribute(client, path, "replication_factor"),
        "annotation": optional_attribute(client, path, "annotation"),
    }
    tablet_state = normalize_tablet_state(optional_attribute(client, path, "tablet_state")) if dynamic else None
    replication: dict[str, Any] | None = None
    delete_items: list[dict[str, Any]] = []

    if is_logical_replicated:
        replicas_value = optional_attribute(client, path, "replicas") or {}
        replicas = manager_replicas(client, path, replicas_value)
        tracker_options = to_jsonable(optional_attribute(client, path, "replicated_table_options") or {})
        if not isinstance(tracker_options, dict):
            tracker_options = {}
        replication = {
            "logicalCluster": cluster,
            "logicalPath": path,
            "globalTrackerEnabled": bool(
                tracker_options.get("enable_replicated_table_tracker", False)
            ),
            "options": tracker_options,
            "replicas": replicas,
        }
        for replica in replicas:
            delete_items.extend([
                {
                    "cluster": "miranda",
                    "path": f"#{replica['id']}",
                    "action": f"Отключить и удалить table_replica ({replica['cluster']})",
                },
                {
                    "cluster": replica["cluster"],
                    "path": replica["path"],
                    "action": "Отмонтировать и удалить физическую таблицу",
                },
            ])
        delete_items.append({"cluster": cluster, "path": path, "action": "Удалить replicated_table"})
    elif upstream_replica_id:
        meta_client = yt_common.build_yt_client(token, "miranda")
        replica = manager_replica_details(meta_client, upstream_replica_id)
        replication = {
            "logicalCluster": "miranda",
            "logicalPath": replica["tablePath"],
            "globalTrackerEnabled": None,
            "options": {},
            "replicas": [replica],
        }
        delete_items = [
            {
                "cluster": "miranda",
                "path": f"#{upstream_replica_id}",
                "action": "Отключить и удалить table_replica",
            },
            {
                "cluster": cluster,
                "path": path,
                "action": "Отмонтировать и удалить физическую таблицу",
            },
        ]
    else:
        delete_items = [{
            "cluster": cluster,
            "path": path,
            "action": "Удалить вместе с содержимым" if is_directory else "Удалить ноду",
        }]

    delete_plan: dict[str, Any] = {"kind": kind, "items": delete_items}
    if is_directory:
        delete_plan["contents"] = manager_directory_preview(client, path)

    replicated_structure = kind in {"replicated_table", "physical_replica"}
    return {
        "cluster": cluster,
        "proxy": f"{cluster}.yt.vk.team",
        "path": path,
        "nodeType": node_type,
        "kind": kind,
        "kindLabel": manager_kind_label(kind),
        "dynamic": dynamic,
        "tabletState": tablet_state,
        "initialAttributes": to_jsonable(initial_attributes),
        "replication": to_jsonable(replication),
        "capabilities": {
            "editAttributes": is_directory or is_table,
            "createLink": is_directory or is_table,
            "copy": (is_directory or is_table) and not replicated_structure,
            "move": (is_directory or is_table) and not replicated_structure,
            "mount": dynamic,
            "replication": replicated_structure,
            "delete": True,
        },
        "restrictions": {
            "copyMove": (
                "Копирование и перемещение replicated-структуры отключено: операция должна "
                "переносить logical table, table_replica и physical tables согласованно."
                if replicated_structure else None
            ),
        },
        "deletePlan": delete_plan,
    }


def manager_validate_destination(
    source_path: str,
    destination_path: str,
    token: str,
    cluster: str,
    operation: str,
) -> dict[str, Any]:
    if operation not in {"link", "copy", "move"}:
        raise ValueError("Неизвестная операция назначения")
    client = yt_common.build_yt_client(token, cluster)
    if not manager_node_exists(client, source_path):
        raise FileNotFoundError(source_path)

    target_exists = manager_node_exists(client, destination_path)
    missing_directories: list[str] = []
    non_directory_nodes: list[dict[str, str]] = []
    # Traversability of the full Cypress path is already guaranteed if the
    # immediate parent exists. Inspecting every ancestor is both redundant and
    # wrong for clusters with special system/portal nodes above a normal folder.
    parent_paths = yt_parent_paths(destination_path)
    immediate_parent = parent_paths[-1] if parent_paths else "//"
    if not manager_node_exists(client, immediate_parent):
        missing_directories.append(immediate_parent)
    else:
        parent_type = str(required_attribute(client, immediate_parent, "type") or "")
        if parent_type not in YT_DIRECTORY_NODE_TYPES:
            non_directory_nodes.append({"path": immediate_parent, "nodeType": parent_type})

    inside_source = destination_path == source_path or destination_path.startswith(f"{source_path}/")
    reasons: list[str] = []
    if target_exists:
        reasons.append("Целевой путь уже занят")
    if missing_directories:
        reasons.append("Не все родительские директории существуют")
    if non_directory_nodes:
        reasons.append("Непосредственный родительский путь не является директорией")
    if operation in {"copy", "move"} and inside_source:
        reasons.append("Нельзя копировать или перемещать ноду внутрь неё самой")

    return {
        "valid": not reasons,
        "cluster": cluster,
        "operation": operation,
        "sourcePath": source_path,
        "destinationPath": destination_path,
        "targetExists": target_exists,
        "missingDirectories": missing_directories,
        "nonDirectoryNodes": non_directory_nodes,
        "reasons": reasons,
    }


def manager_normalize_attribute_changes(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Изменения атрибутов должны быть объектом")
    unknown = set(value) - set(YT_MANAGER_MUTABLE_ATTRIBUTES)
    if unknown:
        raise ValueError(f"Нельзя менять атрибуты: {', '.join(sorted(unknown))}")

    result: dict[str, Any] = {}
    for field, raw_value in value.items():
        if field == "optimizeFor" and raw_value not in {None, "", "lookup", "scan"}:
            raise ValueError("Optimize for должен быть lookup или scan")
        if field == "compressionCodec" and raw_value not in {None, ""}:
            raw_value = validate_compression_codec(raw_value)
        if field == "replicationFactor" and raw_value not in {None, ""}:
            raw_value = integer_setting(raw_value, "Number of replicas", 1, 10)
        if field == "annotation":
            raw_value = str(raw_value or "").strip()
            if len(raw_value) > 1000:
                raise ValueError("Описание не может быть длиннее 1000 символов")
        if field in {"primaryMedium", "tabletCellBundle"} and raw_value not in {None, ""}:
            raw_value = str(raw_value).strip()
        result[field] = raw_value
    return result


def manager_require_source(
    path: str,
    token: str,
    cluster: str,
    *,
    allow_link: bool = False,
) -> dict[str, Any]:
    info = manager_inspect(path, token, cluster)
    if info["kind"] == "link" and not allow_link:
        raise YTManagerConflictError(
            f"{path} является ссылкой на {info['link']['targetPath']}. Для ссылки доступно только удаление."
        )
    return info


def manager_update_attributes(path: str, token: str, cluster: str, changes: Any) -> list[dict[str, Any]]:
    info = manager_require_source(path, token, cluster)
    if not info["capabilities"]["editAttributes"]:
        raise YTManagerConflictError("Для этой ноды изменение атрибутов недоступно")
    normalized = manager_normalize_attribute_changes(changes)
    client = yt_common.build_yt_client(token, cluster)
    completed: list[dict[str, Any]] = []
    try:
        for field, value in normalized.items():
            attribute = YT_MANAGER_MUTABLE_ATTRIBUTES[field]
            attribute_path = f"{path}/@{attribute}"
            if value in {None, ""}:
                if client.exists(attribute_path):
                    client.remove(attribute_path, force=True)
                    completed.append({"action": "remove_attribute", "attribute": attribute})
            else:
                client.set(attribute_path, value)
                completed.append({"action": "set_attribute", "attribute": attribute, "value": value})
    except Exception as error:
        if completed:
            raise YTManagerPartialError(str(error), completed) from error
        raise
    return completed


def manager_mount_action(path: str, token: str, cluster: str, mount: bool) -> list[dict[str, Any]]:
    info = manager_require_source(path, token, cluster)
    if not info["dynamic"]:
        raise YTManagerConflictError("Эта таблица не является динамической")
    state = normalize_tablet_state(info["tabletState"])
    client = yt_common.build_yt_client(token, cluster)
    if mount:
        if state != "unmounted":
            raise YTManagerConflictError(f"Таблица уже находится в состоянии {state}")
        client.mount_table(path, sync=True)
        return [{"action": "mount", "cluster": cluster, "path": path}]
    if state == "unmounted":
        raise YTManagerConflictError("Таблица уже отмонтирована")
    client.unmount_table(path, sync=True)
    return [{"action": "unmount", "cluster": cluster, "path": path}]


def manager_replica_for_action(info: Mapping[str, Any], replica_id: str) -> dict[str, Any]:
    replication = info.get("replication") or {}
    for replica in replication.get("replicas") or []:
        if str(replica.get("id")) == replica_id:
            return replica
    raise YTManagerConflictError("Реплика больше не относится к выбранной таблице")


def manager_update_replica(
    info: Mapping[str, Any],
    token: str,
    replica_id: str,
    state: Any,
    mode: Any,
    tracker_enabled: Any,
) -> list[dict[str, Any]]:
    if not info["capabilities"]["replication"]:
        raise YTManagerConflictError("У выбранной таблицы нет управляемых реплик")
    manager_replica_for_action(info, replica_id)
    if state not in {"enabled", "disabled"}:
        raise ValueError("State должен быть enabled или disabled")
    if mode not in {"sync", "async"}:
        raise ValueError("Mode должен быть sync или async")
    meta_client = yt_common.build_yt_client(token, "miranda")
    meta_client.alter_table_replica(
        replica_id,
        enabled=state == "enabled",
        mode=mode,
        enable_replicated_table_tracker=bool(tracker_enabled),
    )
    return [{
        "action": "alter_table_replica",
        "replicaId": replica_id,
        "state": state,
        "mode": mode,
        "trackerEnabled": bool(tracker_enabled),
    }]


def manager_update_global_tracker(
    info: Mapping[str, Any], token: str, enabled: Any,
) -> list[dict[str, Any]]:
    if info["kind"] != "replicated_table" or info["cluster"] != "miranda":
        raise YTManagerConflictError(
            "Общий переключатель tracker меняется на logical replicated_table в Miranda"
        )
    client = yt_common.build_yt_client(token, "miranda")
    client.set(
        f"{info['path']}/@replicated_table_options/enable_replicated_table_tracker",
        bool(enabled),
    )
    return [{"action": "set_global_tracker", "enabled": bool(enabled)}]


def manager_remove_physical_replica(
    replica: Mapping[str, Any], token: str, completed: list[dict[str, Any]],
) -> None:
    replica_id = str(replica["id"])
    cluster = normalize_cluster(replica["cluster"])
    path = normalize_table_path(replica["path"])
    meta_client = yt_common.build_yt_client(token, "miranda")
    physical_client = yt_common.build_yt_client(token, cluster)

    meta_client.alter_table_replica(replica_id, enabled=False)
    completed.append({"action": "disable_replica", "replicaId": replica_id})
    meta_client.remove(f"#{replica_id}", force=True)
    completed.append({"action": "remove_replica_object", "replicaId": replica_id})

    if manager_node_exists(physical_client, path):
        state = normalize_tablet_state(optional_attribute(physical_client, path, "tablet_state"))
        if state != "unmounted":
            physical_client.unmount_table(path, sync=True)
            completed.append({"action": "unmount", "cluster": cluster, "path": path})
        physical_client.remove(path, recursive=True, force=True)
        completed.append({"action": "remove", "cluster": cluster, "path": path})


def manager_delete(info: Mapping[str, Any], token: str) -> list[dict[str, Any]]:
    completed: list[dict[str, Any]] = []
    try:
        if info["kind"] == "link":
            yt_common.build_yt_client(token, info["cluster"]).remove(info["path"], force=True)
            return [{"action": "remove_link", "cluster": info["cluster"], "path": info["path"]}]

        if info["kind"] == "physical_replica":
            replicas = (info.get("replication") or {}).get("replicas") or []
            if len(replicas) != 1:
                raise YTManagerConflictError("Не удалось однозначно определить table_replica")
            replica = replicas[0]
            if replica.get("cluster") != info["cluster"] or replica.get("path") != info["path"]:
                raise YTManagerConflictError("Метаданные table_replica не совпадают с выбранной таблицей")
            manager_remove_physical_replica(replica, token, completed)
            return completed

        if info["kind"] == "replicated_table":
            for replica in (info.get("replication") or {}).get("replicas") or []:
                manager_remove_physical_replica(replica, token, completed)
            logical_client = yt_common.build_yt_client(token, info["cluster"])
            state = normalize_tablet_state(info.get("tabletState"))
            if state != "unmounted":
                logical_client.unmount_table(info["path"], sync=True)
                completed.append({"action": "unmount", "cluster": info["cluster"], "path": info["path"]})
            logical_client.remove(info["path"], recursive=True, force=True)
            completed.append({"action": "remove", "cluster": info["cluster"], "path": info["path"]})
            return completed

        client = yt_common.build_yt_client(token, info["cluster"])
        if info.get("dynamic") and normalize_tablet_state(info.get("tabletState")) != "unmounted":
            client.unmount_table(info["path"], sync=True)
            completed.append({"action": "unmount", "cluster": info["cluster"], "path": info["path"]})
        client.remove(info["path"], recursive=info["kind"] == "directory", force=True)
        completed.append({"action": "remove", "cluster": info["cluster"], "path": info["path"]})
        return completed
    except Exception as error:
        if isinstance(error, (YTManagerConflictError, YTAccessDeniedError)):
            raise
        if completed:
            raise YTManagerPartialError(str(error), completed) from error
        raise


def manager_destination_action(
    info: Mapping[str, Any], token: str, operation: str, destination_path: str,
) -> list[dict[str, Any]]:
    if not info["capabilities"].get(operation):
        raise YTManagerConflictError(
            info.get("restrictions", {}).get("copyMove")
            or f"Операция {operation} для выбранной ноды недоступна"
        )
    validation = manager_validate_destination(
        info["path"], destination_path, token, info["cluster"], operation,
    )
    if not validation["valid"]:
        raise YTManagerConflictError("; ".join(validation["reasons"]))
    if operation in {"copy", "move"} and info.get("dynamic"):
        state = normalize_tablet_state(info.get("tabletState"))
        if state != "unmounted":
            raise YTManagerConflictError(
                f"Перед {('копированием' if operation == 'copy' else 'перемещением')} "
                f"динамическую таблицу нужно отмонтировать; текущее состояние: {state}"
            )

    client = yt_common.build_yt_client(token, info["cluster"])
    if operation == "link":
        client.link(info["path"], destination_path)
    elif operation == "copy":
        client.copy(info["path"], destination_path, recursive=True)
    else:
        client.move(info["path"], destination_path, recursive=True)
    return [{
        "action": operation,
        "cluster": info["cluster"],
        "sourcePath": info["path"],
        "destinationPath": destination_path,
    }]
