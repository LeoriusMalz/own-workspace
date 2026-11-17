"""Revisioned timer snapshots; elapsed time is derived from a wall-clock anchor."""

from __future__ import annotations

import copy

from workspace.efficiency import storage
from workspace.efficiency.validation import identifier, number, revision, timestamp


def settings(value, media):
    if not isinstance(value, dict):
        raise ValueError("Некорректные настройки Помодоро")
    result = dict(storage.DEFAULT_SETTINGS)
    result.update({key: value[key] for key in result if key in value})
    for key in ("workMinutes", "restMinutes"):
        number(result[key], key, 1 / 60, 1440)
    number(result["totalMinutes"], "Общая длительность", 1 / 60, 10080)
    for key in ("fadeInSeconds", "fadeOutSeconds", "cueStartSeconds", "cueEndSeconds"):
        number(result[key], key, 0, 60)
    for key in ("volume", "cueVolume"):
        number(result[key], key, 0, 1)
    for key in ("infinite", "showDigits"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key}: ожидается переключатель")
    if result["display"] not in ("ring", "bar"):
        raise ValueError("Выберите кольцо или полосу")
    catalog = {item["id"]: item for item in media}
    for key in ("workAudio", "restAudio", "workBackground", "restBackground"):
        media_id = result[key]
        if media_id is None or media_id == "":
            result[key] = None
            continue
        identifier(media_id, "Медиафайл")
        allowed = ("audio",) if key.endswith("Audio") else ("image", "video")
        if media_id not in catalog or catalog[media_id]["kind"] not in allowed:
            raise ValueError(f"Выбранный файл для {key} отсутствует или имеет другой тип")
    return result


def get_timer():
    with storage.transaction() as state:
        return copy.deepcopy(state["timer"])


def put_timer(body):
    if not isinstance(body, dict):
        raise ValueError("Ожидался JSON-объект")
    with storage.transaction(write=True) as state:
        current = state["timer"]
        revision(body.get("revision"), current["revision"])
        prefs = settings(body.get("settings", current["settings"]), state["media"])
        value = body.get("pomodoro", current["pomodoro"])
        if not isinstance(value, dict):
            raise ValueError("Некорректное состояние таймера")
        status = value.get("status")
        if status not in ("idle", "running", "paused", "finished"):
            raise ValueError("Неизвестное состояние таймера")
        elapsed = number(value.get("elapsed"), "Время от начала", 0, 100 * 366 * 86400)
        config = settings(value.get("config", prefs), state["media"])
        anchor = value.get("anchor")
        if status == "running":
            timestamp(anchor, "Момент запуска")
        else:
            anchor = None
        if not config["infinite"]:
            elapsed = min(elapsed, config["totalMinutes"] * 60)
            if elapsed >= config["totalMinutes"] * 60:
                status, anchor = "finished", None
        state["timer"] = {"revision": current["revision"] + 1, "settings": prefs,
                          "pomodoro": {"status": status, "elapsed": elapsed, "anchor": anchor, "config": config}}
        return copy.deepcopy(state["timer"])
