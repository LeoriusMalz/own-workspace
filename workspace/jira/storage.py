"""Atomic file storage for user-defined title components."""

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from workspace.config import BASE_DIR
from workspace.jira.composer import text

OPTIONS_PATH = BASE_DIR / "data" / "jira" / "options.json"
_LOCK = Lock()


def load_options():
    if not OPTIONS_PATH.exists():
        return {"subprojects": [], "kinds": []}
    result = json.loads(OPTIONS_PATH.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or any(
        not isinstance(result.get(key), list) or any(not isinstance(v, str) for v in result[key])
        for key in ("subprojects", "kinds")
    ):
        raise ValueError("Некорректный файл data/jira/options.json")
    return {key: result[key] for key in ("subprojects", "kinds")}


def change_option(category, value, *, remove=False):
    if category not in ("subprojects", "kinds"):
        raise ValueError("Неизвестный список вариантов")
    value = text(value, "Вариант", 80, required=True)
    if any(char in value for char in "\r\n|[]"):
        raise ValueError("Вариант должен быть одной строкой без символов | [ ]")
    with _LOCK:
        options = load_options()
        values = options[category]
        if remove:
            options[category] = [item for item in values if item != value]
        elif value.casefold() not in {item.casefold() for item in values}:
            if len(values) >= 200:
                raise ValueError("В списке уже 200 вариантов")
            values.append(value)
        OPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=OPTIONS_PATH.parent, prefix=".options-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(options, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, OPTIONS_PATH)
        finally:
            Path(name).unlink(missing_ok=True)
        return options
