"""Read-only inspection of YTsaurus tables and directories."""

from __future__ import annotations

from typing import Any

from workspace.config import YT_DIRECTORY_NODE_TYPES, YT_TABLE_NODE_TYPES
from workspace.yt import common as yt_common
from workspace.yt.common import (
    optional_attribute,
    raise_normalized_yt_error,
    required_attribute,
    to_jsonable,
    yson_text,
)


def common_node_attributes(client: Any, path: str) -> dict[str, Any]:
    return {
        "account": optional_attribute(client, path, "account"),
        "owner": optional_attribute(client, path, "owner"),
        "creationTime": optional_attribute(client, path, "creation_time"),
        "modificationTime": optional_attribute(client, path, "modification_time"),
    }


def directory_tables(client: Any, path: str) -> list[dict[str, Any]]:
    try:
        children = client.list(path, attributes=["type", "dynamic"])
    except Exception as error:
        raise_normalized_yt_error(error)

    tables: list[dict[str, Any]] = []
    for child in children:
        name = str(child)
        child_attributes = getattr(child, "attributes", {}) or {}
        node_type = str(child_attributes.get("type") or "")
        if node_type not in YT_TABLE_NODE_TYPES:
            continue
        tables.append({
            "name": name,
            "path": f"{path}/{name}",
            "nodeType": node_type,
            "dynamic": bool(child_attributes.get("dynamic")),
        })

    tables.sort(key=lambda table: table["name"].casefold())
    return tables


def table_info(path: str, token: str, cluster: str) -> dict[str, Any]:
    client = yt_common.build_yt_client(token, cluster)
    try:
        exists = client.exists(path)
    except Exception as error:
        raise_normalized_yt_error(error)
    if not exists:
        raise FileNotFoundError(path)

    node_type = str(required_attribute(client, path, "type"))
    shared_result = {
        "path": path,
        "nodeType": node_type,
        "cluster": cluster,
        "proxy": f"{cluster}.yt.vk.team",
    }

    if node_type in YT_DIRECTORY_NODE_TYPES:
        tables = directory_tables(client, path)
        return {
            **shared_result,
            "kind": "directory",
            "tableCount": len(tables),
            "tables": tables,
            "attributes": to_jsonable(common_node_attributes(client, path)),
        }

    if node_type not in YT_TABLE_NODE_TYPES:
        return {
            **shared_result,
            "kind": "node",
            "attributes": to_jsonable(common_node_attributes(client, path)),
        }

    dynamic = bool(optional_attribute(client, path, "dynamic"))
    schema_raw = required_attribute(client, path, "schema") or []
    schema = to_jsonable(schema_raw)
    if not isinstance(schema, list):
        schema = []

    key_columns = [
        {
            "name": column.get("name"),
            "type": column.get("type_v3", column.get("type")),
            "sortOrder": column.get("sort_order"),
        }
        for column in schema
        if isinstance(column, dict) and column.get("sort_order") is not None
    ]

    replicas = optional_attribute(client, path, "replicas")
    upstream_replica_id = optional_attribute(client, path, "upstream_replica_id")
    replicated = (
        str(node_type) in {"replicated_table", "chaos_replicated_table"}
        or replicas is not None
        or upstream_replica_id not in {None, "", "0-0-0-0"}
    )

    attributes = {
        **common_node_attributes(client, path),
        "rowCount": optional_attribute(client, path, "row_count"),
        "dataWeight": optional_attribute(client, path, "data_weight"),
        "tabletState": optional_attribute(client, path, "tablet_state"),
        "compressionCodec": optional_attribute(client, path, "compression_codec"),
        "erasureCodec": optional_attribute(client, path, "erasure_codec"),
        "optimizeFor": optional_attribute(client, path, "optimize_for"),
        "chunkFormat": optional_attribute(client, path, "chunk_format"),
        "upstreamReplicaId": upstream_replica_id,
        "replicas": replicas,
        "replicatedTableOptions": optional_attribute(client, path, "replicated_table_options"),
    }

    return {
        **shared_result,
        "kind": "table",
        "tableType": "dynamic" if dynamic else "static",
        "dynamic": dynamic,
        "replicated": replicated,
        "schema": schema,
        "schemaYson": yson_text(schema_raw),
        "keyColumns": key_columns,
        "attributes": to_jsonable(attributes),
    }
