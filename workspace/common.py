"""Shared request parsing and application logging."""

from __future__ import annotations

import logging
from typing import Any

from flask import request


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


LOGGER = logging.getLogger("dev-toolbox")


def json_body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("Ожидался JSON-объект")
    return value
