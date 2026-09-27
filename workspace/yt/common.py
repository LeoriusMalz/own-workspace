"""YTsaurus client creation, errors, paths, and value conversion."""

from __future__ import annotations

import json
from typing import Any, Mapping

from workspace.common import LOGGER
from workspace.config import ALLOWED_YT_CLUSTERS


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
