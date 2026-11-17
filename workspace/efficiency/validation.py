"""Shared validation for persisted productivity state."""

from __future__ import annotations

import math
import re


class ConflictError(ValueError):
    pass


def revision(value, expected):
    if type(value) is not int or value != expected:
        raise ConflictError("Данные изменились в другой вкладке. Обновите состояние и повторите действие.")


def number(value, label, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label}: допустимо число от {low:g} до {high:g}")
    return value


def timestamp(value, label):
    number(value, label, 0, 4133980800000)
    if type(value) is not int:
        raise ValueError(f"{label}: ожидается время в миллисекундах")
    return value


def text(value, label, limit=100):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{label}: заполните текст длиной до {limit} символов")
    return value.strip()


def identifier(value, label="Идентификатор"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError(f"{label}: некорректное значение")
    return value


def modes(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 50:
        raise ValueError("Добавьте от 1 до 50 режимов")
    result, ids = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Некорректный режим")
        mode_id = identifier(item.get("id"), "Режим")
        name = text(item.get("name"), "Название режима", 60)
        color = item.get("color")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("Цвет режима должен иметь формат #RRGGBB")
        if mode_id in ids:
            raise ValueError("Идентификаторы режимов должны различаться")
        ids.add(mode_id)
        result.append({"id": mode_id, "name": name, "color": color.lower()})
    return result
