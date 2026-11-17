"""Independent activity sessions, revisioned transitions, and timeline editing."""

from __future__ import annotations

import copy
import time
import uuid

from workspace.efficiency import storage
from workspace.efficiency.validation import ConflictError, identifier, modes, revision, timestamp


def now_ms():
    return time.time_ns() // 1_000_000


def totals(session, until=None):
    result = {}
    for segment in session["segments"]:
        end = segment["end"] if segment["end"] is not None else (now_ms() if until is None else until)
        seconds = max(0, end - segment["start"]) / 1000
        mode_id = segment["modeId"]
        result[mode_id] = result.get(mode_id, 0) + seconds
    return {key: round(value, 3) for key, value in result.items()}


def decorate(session, until=None):
    result = copy.deepcopy(session)
    result["totals"] = totals(session, until)
    result["durationSeconds"] = round(sum(result["totals"].values()), 3)
    return result


def get_tracker():
    with storage.transaction() as state:
        return copy.deepcopy(state["tracker"])


def put_modes(body):
    values = modes(body.get("modes"))
    with storage.transaction(write=True) as state:
        tracker = state["tracker"]
        revision(body.get("revision"), tracker["revision"])
        active = tracker["activeSession"]
        if active and active["segments"][-1]["modeId"] not in {item["id"] for item in values}:
            raise ValueError("Сначала переключитесь в другой режим, затем удалите текущий")
        tracker["modes"] = values
        tracker["revision"] += 1
        if active:
            by_id = {item["id"]: item for item in active["modes"]}
            by_id.update({item["id"]: item for item in values})
            active["modes"] = list(by_id.values())
            active["revision"] += 1
        return copy.deepcopy(tracker)


def action(body):
    operation = body.get("action")
    if operation not in ("start", "switch", "finish"):
        raise ValueError("Неизвестное действие трекера")
    with storage.transaction(write=True) as state:
        tracker = state["tracker"]
        revision(body.get("revision"), tracker["revision"])
        active = tracker["activeSession"]
        if operation == "start" and active:
            raise ConflictError("День уже начат в другой вкладке")
        if operation != "start" and not active:
            raise ConflictError("Нет активного дня")
        mode_id = body.get("modeId")
        if operation in ("start", "switch"):
            if mode_id is None and operation == "start":
                mode_id = tracker["modes"][0]["id"]
            identifier(mode_id, "Режим")
            if mode_id not in {item["id"] for item in tracker["modes"]}:
                raise ValueError("Выберите существующий режим")
        instant = now_ms()
        if active:
            instant = max(instant, active["segments"][-1]["start"])
        if operation == "start":
            active = {"id": uuid.uuid4().hex, "revision": 1, "start": instant, "end": None,
                      "modes": copy.deepcopy(tracker["modes"]),
                      "segments": [{"id": uuid.uuid4().hex, "modeId": mode_id, "start": instant, "end": None}]}
            tracker["activeSession"] = active
        elif operation == "switch":
            if active["segments"][-1]["modeId"] != mode_id:
                active["segments"][-1]["end"] = instant
                active["segments"].append({"id": uuid.uuid4().hex, "modeId": mode_id, "start": instant, "end": None})
                active["revision"] += 1
        else:
            active["end"] = instant
            active["segments"][-1]["end"] = instant
            active["revision"] += 1
            state["sessions"].append(active)
            tracker["activeSession"] = None
        tracker["revision"] += 1
        result = copy.deepcopy(tracker)
        if operation == "finish":
            result["finishedSession"] = decorate(active)
        return result


def list_sessions():
    with storage.transaction() as state:
        result = []
        for session in sorted(state["sessions"], key=lambda item: item["start"], reverse=True):
            result.append({key: value for key, value in decorate(session).items() if key != "segments"})
        return result


def get_session(session_id):
    identifier(session_id, "Сессия")
    with storage.transaction() as state:
        for session in state["sessions"]:
            if session["id"] == session_id:
                return decorate(session)
    raise FileNotFoundError("Сессия не найдена")


def edit_session(session_id, body):
    identifier(session_id, "Сессия")
    with storage.transaction(write=True) as state:
        session = next((item for item in state["sessions"] if item["id"] == session_id), None)
        if session is None:
            raise FileNotFoundError("Сессия не найдена")
        revision(body.get("revision"), session["revision"])
        start = timestamp(body.get("start"), "Начало сессии")
        end = timestamp(body.get("end"), "Конец сессии")
        if end < start:
            raise ValueError("Конец сессии не может быть раньше начала")
        session_modes = modes(body.get("modes", session["modes"]))
        mode_ids = {item["id"] for item in session_modes}
        segments = body.get("segments")
        if not isinstance(segments, list) or not 1 <= len(segments) <= 10000:
            raise ValueError("Сессия должна содержать от 1 до 10000 промежутков")
        cleaned, ids, previous_end = [], set(), start
        for segment in segments:
            if not isinstance(segment, dict):
                raise ValueError("Некорректный промежуток")
            segment_id = identifier(segment.get("id"), "Промежуток")
            if segment_id in ids:
                raise ValueError("Идентификаторы промежутков должны различаться")
            ids.add(segment_id)
            mode_id = segment.get("modeId")
            identifier(mode_id, "Режим")
            if mode_id not in mode_ids:
                raise ValueError("Режим промежутка отсутствует в этой сессии")
            segment_start = timestamp(segment.get("start"), "Начало промежутка")
            segment_end = timestamp(segment.get("end"), "Конец промежутка")
            if segment_start != previous_end or segment_end < segment_start or segment_end > end:
                raise ValueError("Промежутки должны идти подряд, без пропусков и пересечений, внутри сессии")
            cleaned.append({"id": segment_id, "modeId": mode_id, "start": segment_start, "end": segment_end})
            previous_end = segment_end
        if previous_end != end:
            raise ValueError("Последний промежуток должен заканчиваться вместе с сессией")
        session.update(start=start, end=end, segments=cleaned, modes=session_modes, revision=session["revision"] + 1)
        return decorate(session)
