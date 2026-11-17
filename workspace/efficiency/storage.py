"""Atomic JSON transactions, guarded across Flask threads and worker processes."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from workspace.config import BASE_DIR

DATA_DIR = BASE_DIR / "data" / "efficiency"
_LOCK = RLock()

DEFAULT_SETTINGS = {
    "workMinutes": 25, "restMinutes": 5, "totalMinutes": 120,
    "infinite": False, "display": "ring", "showDigits": True,
    "workAudio": None, "restAudio": None,
    "workBackground": None, "restBackground": None,
    "volume": 0.5, "fadeInSeconds": 3, "fadeOutSeconds": 3,
    "cueStartSeconds": 5, "cueEndSeconds": 5, "cueVolume": 0.6,
}
DEFAULT_MODES = [
    {"id": "work", "name": "Работа", "color": "#70a7ff"},
    {"id": "rest", "name": "Отдых", "color": "#54d49b"},
]


def initial_state():
    return {
        "version": 1,
        "timer": {"revision": 0, "settings": copy.deepcopy(DEFAULT_SETTINGS),
                  "pomodoro": {"status": "idle", "elapsed": 0, "anchor": None,
                               "config": copy.deepcopy(DEFAULT_SETTINGS)}},
        "tracker": {"revision": 0, "modes": copy.deepcopy(DEFAULT_MODES), "activeSession": None},
        "sessions": [], "media": [],
    }


@contextmanager
def transaction(*, write=False):
    with _LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (DATA_DIR / ".lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                path = DATA_DIR / "state.json"
                state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else initial_state()
                if not isinstance(state, dict) or state.get("version") != 1:
                    raise ValueError("Неподдерживаемый формат data/efficiency/state.json")
                yield state
                if write:
                    fd, name = tempfile.mkstemp(dir=DATA_DIR, prefix=".state-", suffix=".tmp")
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as file:
                            json.dump(state, file, ensure_ascii=False, allow_nan=False, indent=2)
                            file.write("\n")
                            file.flush()
                            os.fsync(file.fileno())
                        os.replace(name, path)
                    finally:
                        Path(name).unlink(missing_ok=True)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
