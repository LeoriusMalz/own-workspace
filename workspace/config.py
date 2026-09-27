"""Paths, limits, and supported YTsaurus settings."""

from __future__ import annotations

import os
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
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
