from __future__ import annotations

import json
import hashlib
import logging
import math
import mimetypes
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data" / "notes"
DRAFTS_DIR = DATA_DIR / "drafts"
ARTICLES_DIR = DATA_DIR / "articles"
UPLOADS_DIR = DATA_DIR / "uploads"

ALLOWED_YT_CLUSTERS = {"jupiter", "saturn", "miranda"}
MAX_NOTE_BYTES = int(os.environ.get("MAX_NOTE_BYTES", 5 * 1024 * 1024))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
YT_TABLE_NODE_TYPES = {"table", "replicated_table", "chaos_replicated_table"}
YT_DIRECTORY_NODE_TYPES = {"map_node", "list_node", "portal_entrance", "scion"}
YT_CREATOR_TABLE_KINDS = {"static", "dynamic"}
YT_CREATOR_PRIMITIVE_TYPES = {
    "int64", "int32", "int16", "int8",
    "uint64", "uint32", "uint16", "uint8",
    "double", "float", "bool", "boolean", "string", "utf8", "json", "uuid",
    "date", "datetime", "timestamp", "interval",
    "date32", "datetime64", "timestamp64", "interval64",
    "null", "void", "yson", "any",
}
YT_CREATOR_COMPLEX_TYPES = {
    "optional", "list", "struct", "tuple", "variant", "dict", "tagged", "decimal",
}
YT_CREATOR_CLUSTERS = {"jupiter", "saturn", "miranda"}
YT_REPLICA_CLUSTERS = {"jupiter", "saturn"}
MAX_SCHEMA_COLUMNS = 1000
MAX_SCHEMA_TEXT_BYTES = 512 * 1024
YT_MANAGER_MUTABLE_ATTRIBUTES = {
    "primaryMedium": "primary_medium",
    "tabletCellBundle": "tablet_cell_bundle",
    "optimizeFor": "optimize_for",
    "compressionCodec": "compression_codec",
    "replicationFactor": "replication_factor",
    "annotation": "annotation",
}
YT_MANAGER_TABLET_ACTIVE_STATES = {
    "mounted", "mounting", "frozen", "freezing", "unmounting",
}
MAX_MANAGER_DELETE_PREVIEW = 200
YT_MUTATOR_SUPPORTED_TYPES = {
    "int64", "int32", "int16", "int8",
    "uint64", "uint32", "uint16", "uint8",
    "double", "float", "bool", "string", "utf8", "json", "uuid",
    "date", "datetime", "timestamp", "interval",
    "date32", "datetime64", "timestamp64", "interval64", "yson",
}
YT_MUTATOR_SIGNED_INTEGER_TYPES = ("int8", "int16", "int32", "int64")
YT_MUTATOR_UNSIGNED_INTEGER_TYPES = ("uint8", "uint16", "uint32", "uint64")
YT_MUTATOR_INTEGER_RANGES = {
    "int64": (-(2**63), 2**63 - 1),
    "int32": (-(2**31), 2**31 - 1),
    "int16": (-(2**15), 2**15 - 1),
    "int8": (-(2**7), 2**7 - 1),
    "uint64": (0, 2**64 - 1),
    "uint32": (0, 2**32 - 1),
    "uint16": (0, 2**16 - 1),
    "uint8": (0, 2**8 - 1),
    "date": (0, 49673 - 1),
    "datetime": (0, 49673 * 86400 - 1),
    "timestamp": (0, 49673 * 86400 * 10**6 - 1),
    "interval": (-(49673 * 86400 * 10**6) + 1, 49673 * 86400 * 10**6 - 1),
    "date32": (-53375809, 53375808 - 1),
    "datetime64": (-53375809 * 86400, 53375808 * 86400 - 1),
    "timestamp64": (-53375809 * 86400 * 10**6, 53375808 * 86400 * 10**6 - 1),
    "interval64": (-9223339708800000000, 9223339708800000000),
}
YT_MUTATOR_MAX_QUERY_LIMIT = 500
YT_MUTATOR_MAX_WHERE_BYTES = 16 * 1024


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
LOGGER = logging.getLogger("dev-toolbox")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = max(MAX_NOTE_BYTES, MAX_UPLOAD_BYTES) + 1024 * 1024


RU_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_directories() -> None:
    for directory in (DRAFTS_DIR, ARTICLES_DIR, UPLOADS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


ensure_directories()


def make_slug(value: str, fallback: str = "note") -> str:
    transliterated = "".join(RU_TO_LATIN.get(char, char) for char in value.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    return slug[:90].rstrip("-") or fallback


def unique_slug(directory: Path, base_slug: str, *, except_slug: str | None = None) -> str:
    candidate = base_slug
    suffix = 2
    while candidate != except_slug and (directory / f"{candidate}.json").exists():
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


def note_path(kind: str, slug: str) -> Path:
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("Некорректный slug заметки")
    if kind == "drafts":
        return DRAFTS_DIR / f"{slug}.json"
    if kind == "articles":
        return ARTICLES_DIR / f"{slug}.json"
    raise ValueError("Неизвестный тип заметки")


def read_json_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} должен содержать JSON-объект")
    return value


def write_json_file(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    if len(payload.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValueError("Заметка слишком большая")

    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def note_summary(note: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "slug": note.get("slug"),
        "title": note.get("title") or "Без названия",
        "kind": note.get("kind"),
        "updatedAt": note.get("updatedAt"),
        "createdAt": note.get("createdAt"),
        "sourceArticleSlug": note.get("sourceArticleSlug"),
    }


def list_notes(directory: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            result.append(note_summary(read_json_file(path)))
        except Exception:
            LOGGER.exception("Failed to read note file %s", path)
    result.sort(key=lambda note: str(note.get("updatedAt") or ""), reverse=True)
    return result


def json_body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("Ожидался JSON-объект")
    return value


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {str(to_jsonable(key)): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return str(value)


def yson_text(value: Any) -> str:
    try:
        import yt.yson as yson

        return yson.dumps(value, yson_format="pretty").decode("utf-8")
    except Exception:
        return json.dumps(to_jsonable(value), ensure_ascii=False, indent=2)


def normalize_cluster(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Кластер YT не выбран")
    cluster = value.strip().lower()
    if cluster not in ALLOWED_YT_CLUSTERS:
        raise ValueError("Поддерживаются только кластеры jupiter, saturn и miranda")
    return cluster


def build_yt_client(token: str, cluster: str):
    if not token:
        raise PermissionError("YT-токен не установлен")

    try:
        import yt.wrapper as yt
    except ModuleNotFoundError as error:
        raise RuntimeError("Не установлен пакет ytsaurus-client") from error

    return yt.YtClient(proxy=f"{cluster}.yt.vk.team", token=token)


class YTAccessDeniedError(PermissionError):
    """The selected token cannot read the requested YT node."""


class YTCreationError(RuntimeError):
    def __init__(self, message: str, created: list[dict[str, Any]]):
        super().__init__(message)
        self.created = created


class YTManagerConflictError(RuntimeError):
    """The source or destination changed after the user inspected it."""


class YTManagerPartialError(RuntimeError):
    def __init__(self, message: str, completed: list[dict[str, Any]]):
        super().__init__(message)
        self.completed = completed


def is_yt_access_denied(error: Exception) -> bool:
    error_name = type(error).__name__.lower()
    error_text = str(error).lower()
    markers = (
        "authorization",
        "authentication",
        "access denied",
        "permission denied",
        "not enough permissions",
        "has no permissions",
        "request is not permitted",
        "unauthorized",
        "forbidden",
        "invalid token",
    )
    return (
        "authorization" in error_name
        or "authentication" in error_name
        or "permission" in error_name
        or any(marker in error_text for marker in markers)
    )


def raise_normalized_yt_error(error: Exception) -> None:
    if is_yt_access_denied(error):
        raise YTAccessDeniedError("Доступ ограничен для данного токена") from error
    raise error


def optional_attribute(client: Any, path: str, name: str) -> Any:
    attribute_path = f"{path}/@{name}"
    try:
        if not client.exists(attribute_path):
            return None
        return client.get(attribute_path)
    except Exception as error:
        if is_yt_access_denied(error):
            raise YTAccessDeniedError("Доступ ограничен для данного токена") from error
        LOGGER.debug("YT attribute is unavailable: %s", attribute_path, exc_info=True)
        return None


def required_attribute(client: Any, path: str, name: str) -> Any:
    try:
        return client.get(f"{path}/@{name}")
    except Exception as error:
        raise_normalized_yt_error(error)


def normalize_table_path(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("YT-путь должен быть строкой")
    path = value.strip()
    if not path.startswith("//"):
        raise ValueError("YT-путь должен начинаться с //")
    if len(path) > 2048 or "\x00" in path or "/@" in path:
        raise ValueError("Некорректный YT-путь")
    normalized = path.rstrip("/")
    if len(normalized) <= 2:
        raise ValueError("Укажите путь ниже корня YT, например //home/project")
    if any(not segment for segment in normalized[2:].split("/")):
        raise ValueError("YT-путь не должен содержать пустые части")
    return normalized


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
    client = build_yt_client(token, cluster)
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
        client = build_yt_client(token, cluster)
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
    client = build_yt_client(token, str(config["cluster"]))
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
    client = build_yt_client(token, str(config["cluster"]))
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
        client = build_yt_client(token, str(config["cluster"]))
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

    meta_client = build_yt_client(token, "miranda")
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
        replica_client = build_yt_client(token, cluster)
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
            build_yt_client(token, cluster).mount_table(path, sync=True)
    return created


def create_yt_table(config: Mapping[str, Any], token: str) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    try:
        return _create_yt_table(config, token, created)
    except Exception as error:
        if created:
            raise YTCreationError(str(error), created) from error
        raise


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
    client = build_yt_client(token, cluster)
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
        meta_client = build_yt_client(token, "miranda")
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
    client = build_yt_client(token, cluster)
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
    client = build_yt_client(token, cluster)
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
    client = build_yt_client(token, cluster)
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
    meta_client = build_yt_client(token, "miranda")
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
    client = build_yt_client(token, "miranda")
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
    meta_client = build_yt_client(token, "miranda")
    physical_client = build_yt_client(token, cluster)

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
            build_yt_client(token, info["cluster"]).remove(info["path"], force=True)
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
            logical_client = build_yt_client(token, info["cluster"])
            state = normalize_tablet_state(info.get("tabletState"))
            if state != "unmounted":
                logical_client.unmount_table(info["path"], sync=True)
                completed.append({"action": "unmount", "cluster": info["cluster"], "path": info["path"]})
            logical_client.remove(info["path"], recursive=True, force=True)
            completed.append({"action": "remove", "cluster": info["cluster"], "path": info["path"]})
            return completed

        client = build_yt_client(token, info["cluster"])
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

    client = build_yt_client(token, info["cluster"])
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

    client = build_yt_client(token, cluster)
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
        client = build_yt_client(token, cluster)
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
        target_client = build_yt_client(token, target["cluster"])
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
                    build_yt_client(token, target["cluster"]).unmount_table(target["path"], sync=True)
                    unmounted.append(target)
                    completed.append({"action": "unmount", "cluster": target["cluster"], "path": target["path"]})

        for target in targets:
            build_yt_client(token, target["cluster"]).alter_table(target["path"], schema=schema_value)
            completed.append({"action": "alter_table", "cluster": target["cluster"], "path": target["path"]})
    except Exception as error:
        action_error = error
    finally:
        # A failed alter must not leave tables unexpectedly unmounted. Restore
        # every target whose original state we changed, including replicas.
        for target in unmounted:
            try:
                build_yt_client(token, target["cluster"]).mount_table(
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
    client = build_yt_client(token, info["cluster"])
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
                build_yt_client(token, info["cluster"]), info["path"], keys,
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

    client = build_yt_client(token, info["cluster"])
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
    client = build_yt_client(token, info["cluster"])
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


@app.errorhandler(413)
def payload_too_large(_: Exception):
    return jsonify({"error": "Файл или запрос слишком большой"}), 413


@app.after_request
def security_headers(response):
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "ytClusters": sorted(ALLOWED_YT_CLUSTERS)})


@app.post("/api/yt/table-info")
def api_table_info():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        return jsonify({"result": table_info(path, token, cluster)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица или папка не найдена", "code": "node_not_found"}), 404
    except YTAccessDeniedError:
        return jsonify({
            "error": "Доступ ограничен для данного токена",
            "code": "access_denied",
        }), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT table inspection failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/creator/schema/convert")
def api_creator_schema_convert():
    try:
        body = json_body()
        schema = parse_schema_text(body.get("text"), body.get("format"))
        return jsonify({"result": schema_conversion_result(schema)})
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_schema"}), 400
    except Exception as error:
        LOGGER.exception("YT schema conversion failed")
        return jsonify({"error": str(error), "code": "schema_conversion_error"}), 500


@app.post("/api/yt/creator/validate")
def api_creator_validate():
    try:
        body = json_body()
        config = normalize_creator_config(body, require_schema_rules=False)
        token = request.headers.get("X-YT-Token", "").strip()
        result = validate_creator_destinations(config, token)
        return jsonify({"result": result})
    except YTAccessDeniedError:
        return jsonify({
            "error": "Доступ ограничен для данного токена",
            "code": "access_denied",
        }), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT creator path validation failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/creator/create")
def api_creator_create():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Создание таблицы не подтверждено")
        config = normalize_creator_config(body, require_schema_rules=True)
        token = request.headers.get("X-YT-Token", "").strip()
        validation = validate_creator_destinations(config, token)
        if not validation["valid"]:
            return jsonify({
                "error": "Создание невозможно: путь изменился или таблица уже существует",
                "code": "destination_invalid",
                "details": validation,
            }), 409
        created = create_yt_table(config, token)
        return jsonify({"result": {"created": created}}), 201
    except YTCreationError as error:
        LOGGER.exception("YT table creation stopped after partial success")
        return jsonify({
            "error": (
                "Создание завершилось частично. Не удаляйте созданные объекты автоматически; "
                f"проверьте список и исходную ошибку: {error}"
            ),
            "code": "partial_creation",
            "created": error.created,
        }), 502
    except YTAccessDeniedError:
        return jsonify({
            "error": "Доступ ограничен для данного токена",
            "code": "access_denied",
        }), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT table creation failed")
        error_text = str(error)
        code = "already_exists" if "already exists" in error_text.lower() else "creation_failed"
        status = 409 if code == "already_exists" else 502
        return jsonify({"error": error_text, "code": code}), status


@app.post("/api/yt/creator/directory/validate")
def api_creator_directory_validate():
    try:
        config = normalize_directory_config(json_body())
        token = request.headers.get("X-YT-Token", "").strip()
        return jsonify({"result": inspect_directory_chain(config, token)})
    except YTAccessDeniedError:
        return jsonify({
            "error": "Доступ ограничен для данного токена",
            "code": "access_denied",
        }), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT directory chain validation failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/creator/directory/create")
def api_creator_directory_create():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Создание директорий не подтверждено")
        config = normalize_directory_config(body)
        token = request.headers.get("X-YT-Token", "").strip()
        plan = inspect_directory_chain(config, token)
        if not plan["valid"]:
            return jsonify({
                "error": plan["reason"] or "Создание директорий невозможно",
                "code": "destination_invalid",
                "details": plan,
            }), 409
        created = create_directory_chain(config, token, plan)
        return jsonify({"result": {"created": created}}), 201
    except YTCreationError as error:
        LOGGER.exception("YT directory creation stopped after partial success")
        return jsonify({
            "error": (
                "Цепочка директорий создана частично. Проверьте уже созданные пути: "
                f"{error}"
            ),
            "code": "partial_creation",
            "created": error.created,
        }), 502
    except YTAccessDeniedError:
        return jsonify({
            "error": "Доступ ограничен для данного токена",
            "code": "access_denied",
        }), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT directory creation failed")
        error_text = str(error)
        code = "already_exists" if "already exists" in error_text.lower() else "creation_failed"
        status = 409 if code == "already_exists" else 502
        return jsonify({"error": error_text, "code": code}), status


@app.post("/api/yt/manager/inspect")
def api_manager_inspect():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        return jsonify({"result": manager_inspect(path, token, cluster)})
    except FileNotFoundError:
        return jsonify({"error": "YT-нода не найдена", "code": "node_not_found"}), 404
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Manager inspection failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/manager/validate-destination")
def api_manager_validate_destination():
    try:
        body = json_body()
        source_path = normalize_table_path(body.get("sourcePath"))
        destination_path = normalize_table_path(body.get("destinationPath"))
        cluster = normalize_cluster(body.get("cluster"))
        operation = str(body.get("operation") or "")
        token = request.headers.get("X-YT-Token", "").strip()
        return jsonify({
            "result": manager_validate_destination(
                source_path, destination_path, token, cluster, operation,
            ),
        })
    except FileNotFoundError:
        return jsonify({"error": "Исходная YT-нода не найдена", "code": "node_not_found"}), 404
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Manager destination validation failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/manager/action")
def api_manager_action():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Операция не подтверждена")
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        action = str(body.get("action") or "")
        token = request.headers.get("X-YT-Token", "").strip()
        info = manager_require_source(path, token, cluster, allow_link=action == "delete")

        if action == "update_attributes":
            completed = manager_update_attributes(path, token, cluster, body.get("changes"))
        elif action in {"link", "copy", "move"}:
            destination_path = normalize_table_path(body.get("destinationPath"))
            completed = manager_destination_action(info, token, action, destination_path)
        elif action == "mount":
            completed = manager_mount_action(path, token, cluster, True)
        elif action == "unmount":
            completed = manager_mount_action(path, token, cluster, False)
        elif action == "update_replica":
            completed = manager_update_replica(
                info,
                token,
                str(body.get("replicaId") or ""),
                body.get("state"),
                body.get("mode"),
                body.get("trackerEnabled"),
            )
        elif action == "update_global_tracker":
            completed = manager_update_global_tracker(info, token, body.get("enabled"))
        elif action == "delete":
            if body.get("doubleConfirmed") is not True or body.get("confirmationText") != path:
                raise ValueError("Для удаления нужно повторно ввести точный путь YT-ноды")
            completed = manager_delete(info, token)
        else:
            raise ValueError("Неизвестная операция YT Manager")

        return jsonify({"result": {"completed": to_jsonable(completed)}})
    except FileNotFoundError:
        return jsonify({"error": "YT-нода больше не существует", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "state_conflict"}), 409
    except YTManagerPartialError as error:
        LOGGER.exception("YT Manager action stopped after partial success")
        return jsonify({
            "error": f"Операция выполнена частично: {error}",
            "code": "partial_action",
            "completed": to_jsonable(error.completed),
        }), 502
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Manager action failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/mutator/inspect")
def api_mutator_inspect():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        return jsonify({"result": mutator_table_info(path, token, cluster)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator inspection failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/mutator/schema/plan")
def api_mutator_schema_plan():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        return jsonify({"result": mutator_schema_plan(info, body.get("change"), token)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "schema_incompatible"}), 409
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator schema plan failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/mutator/schema/apply")
def api_mutator_schema_apply():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Изменение схемы не подтверждено")
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        if str(body.get("expectedSchemaHash") or "") != info["schemaHash"]:
            raise YTManagerConflictError("Схема таблицы изменилась после загрузки. Обновите данные.")
        plan = mutator_schema_plan(info, body.get("change"), token)
        return jsonify({"result": mutator_apply_schema(info, plan, token)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "schema_incompatible"}), 409
    except YTManagerPartialError as error:
        LOGGER.exception("YT Mutator schema apply stopped after partial success")
        return jsonify({
            "error": f"Схема изменена частично: {error}",
            "code": "partial_action",
            "completed": to_jsonable(error.completed),
        }), 502
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator schema apply failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/mutator/data/search")
def api_mutator_data_search():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        return jsonify({"result": mutator_search_rows(info, token, body)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "operation_unavailable"}), 409
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator search failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/mutator/data/validate-inserts")
def api_mutator_validate_inserts():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        validation = mutator_validate_insert_rows(info, token, body.get("rows"))
        validation.pop("parsedRows", None)
        return jsonify({"result": validation})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "operation_unavailable"}), 409
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator insert validation failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/mutator/data/apply")
def api_mutator_data_apply():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Изменения строк не подтверждены")
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        if str(body.get("expectedSchemaHash") or "") != info["schemaHash"]:
            raise YTManagerConflictError("Схема таблицы изменилась. Обновите результаты поиска.")
        return jsonify({"result": mutator_apply_data_changes(info, token, body)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "state_conflict"}), 409
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator data apply failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.post("/api/yt/mutator/data/insert")
def api_mutator_data_insert():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Добавление строк не подтверждено")
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        if str(body.get("expectedSchemaHash") or "") != info["schemaHash"]:
            raise YTManagerConflictError("Схема таблицы изменилась. Перезагрузите форму.")
        return jsonify({"result": mutator_insert_rows(info, token, body.get("rows"))})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "state_conflict"}), 409
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator insert failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@app.get("/api/notes")
def api_list_notes():
    return jsonify({
        "drafts": list_notes(DRAFTS_DIR),
        "articles": list_notes(ARTICLES_DIR),
    })


@app.post("/api/notes/drafts")
def api_create_draft():
    now = utc_now()
    slug = unique_slug(
        DRAFTS_DIR,
        f"draft-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}",
    )
    note = {
        "slug": slug,
        "title": "Новый черновик",
        "kind": "draft",
        "content": "<p></p>",
        "createdAt": now,
        "updatedAt": now,
        "sourceArticleSlug": None,
    }
    write_json_file(note_path("drafts", slug), note)
    return jsonify({"result": note}), 201


@app.get("/api/notes/<kind>/<slug>")
def api_get_note(kind: str, slug: str):
    try:
        path = note_path(kind, slug)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if not path.exists():
        return jsonify({"error": "Заметка не найдена"}), 404
    return jsonify({"result": read_json_file(path)})


@app.put("/api/notes/drafts/<slug>")
def api_update_draft(slug: str):
    try:
        path = note_path("drafts", slug)
        if not path.exists():
            return jsonify({"error": "Черновик не найден"}), 404
        body = json_body()
        note = read_json_file(path)
        title = str(body.get("title", note.get("title", "Новый черновик"))).strip()
        content = body.get("content", note.get("content", "<p></p>"))
        if not isinstance(content, str):
            raise ValueError("Содержимое заметки должно быть строкой")
        note.update({
            "title": title[:200] or "Новый черновик",
            "content": content,
            "updatedAt": utc_now(),
        })
        write_json_file(path, note)
        return jsonify({"result": note_summary(note)})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.post("/api/notes/articles/<slug>/edit")
def api_edit_article(slug: str):
    try:
        article_path = note_path("articles", slug)
        if not article_path.exists():
            return jsonify({"error": "Статья не найдена"}), 404
        for existing_path in DRAFTS_DIR.glob("*.json"):
            existing = read_json_file(existing_path)
            if existing.get("sourceArticleSlug") == slug:
                return jsonify({"result": existing})
        article = read_json_file(article_path)
        draft_slug = unique_slug(DRAFTS_DIR, f"{slug}-edit")
        now = utc_now()
        draft = {
            **article,
            "slug": draft_slug,
            "kind": "draft",
            "createdAt": now,
            "updatedAt": now,
            "sourceArticleSlug": slug,
        }
        write_json_file(note_path("drafts", draft_slug), draft)
        return jsonify({"result": draft}), 201
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.post("/api/notes/drafts/<slug>/publish")
def api_publish_draft(slug: str):
    try:
        draft_path = note_path("drafts", slug)
        if not draft_path.exists():
            return jsonify({"error": "Черновик не найден"}), 404
        body = json_body()
        title = str(body.get("title", "")).strip()
        if not title:
            raise ValueError("Введите название статьи")

        draft = read_json_file(draft_path)
        source_slug = draft.get("sourceArticleSlug")
        article_slug = (
            str(source_slug)
            if source_slug
            else unique_slug(ARTICLES_DIR, make_slug(title))
        )
        article_path = note_path("articles", article_slug)
        now = utc_now()
        article = {
            **draft,
            "slug": article_slug,
            "title": title[:200],
            "kind": "article",
            "updatedAt": now,
            "publishedAt": draft.get("publishedAt") or now,
            "sourceArticleSlug": None,
        }
        write_json_file(article_path, article)
        draft_path.unlink()
        return jsonify({"result": article})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.post("/api/notes/drafts/<slug>/cancel")
def api_cancel_edit(slug: str):
    try:
        draft_path = note_path("drafts", slug)
        if not draft_path.exists():
            return jsonify({"error": "Черновик не найден"}), 404
        draft = read_json_file(draft_path)
        source_slug = draft.get("sourceArticleSlug")
        if not source_slug:
            raise ValueError("Это обычный черновик — его можно удалить через кнопку «Удалить»")
        draft_path.unlink()
        return jsonify({"result": {"articleSlug": source_slug}})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@app.delete("/api/notes/<kind>/<slug>")
def api_delete_note(kind: str, slug: str):
    try:
        path = note_path(kind, slug)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if not path.exists():
        return jsonify({"error": "Заметка не найдена"}), 404
    path.unlink()
    return jsonify({"result": {"deleted": True}})


@app.post("/api/notes/uploads")
def api_upload_image():
    file = request.files.get("image")
    if file is None or not file.filename:
        return jsonify({"error": "Изображение не выбрано"}), 400

    original_name = secure_filename(file.filename)
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({"error": "Поддерживаются PNG, JPG, GIF, WEBP и SVG"}), 400

    mime_type = file.mimetype or mimetypes.guess_type(original_name)[0] or ""
    if not mime_type.startswith("image/"):
        return jsonify({"error": "Файл не является изображением"}), 400

    filename = f"{uuid.uuid4().hex}{extension}"
    target = UPLOADS_DIR / filename
    file.save(target)
    if target.stat().st_size > MAX_UPLOAD_BYTES:
        target.unlink(missing_ok=True)
        return jsonify({"error": "Изображение слишком большое"}), 413
    return jsonify({"result": {"url": f"/notes-media/{filename}"}}), 201


@app.get("/notes-media/<filename>")
def notes_media(filename: str):
    return send_from_directory(UPLOADS_DIR, secure_filename(filename))


@app.get("/assets/<path:filename>")
def assets(filename: str):
    return send_from_directory(STATIC_DIR / "assets", filename)


@app.get("/styles.css")
def styles():
    return send_from_directory(STATIC_DIR, "styles.css")


@app.get("/yt-creator.css")
def yt_creator_styles():
    return send_from_directory(STATIC_DIR, "yt-creator.css")


@app.get("/yt-manager.css")
def yt_manager_styles():
    return send_from_directory(STATIC_DIR, "yt-manager.css")


@app.get("/yt-mutator.css")
def yt_mutator_styles():
    return send_from_directory(STATIC_DIR, "yt-mutator.css")


@app.get("/app.js")
def javascript():
    return send_from_directory(STATIC_DIR, "app.js")


@app.get("/yt-creator.js")
def yt_creator_javascript():
    return send_from_directory(STATIC_DIR, "yt-creator.js")


@app.get("/yt-manager.js")
def yt_manager_javascript():
    return send_from_directory(STATIC_DIR, "yt-manager.js")


@app.get("/yt-mutator.js")
def yt_mutator_javascript():
    return send_from_directory(STATIC_DIR, "yt-mutator.js")


@app.get("/")
@app.get("/tools")
@app.get("/notes")
@app.get("/tools/<path:_>")
@app.get("/notes/<path:_>")
def spa(_: str | None = None):
    return send_from_directory(STATIC_DIR, "index.html")


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
