"""Split session intervals by local calendar day, then aggregate by ISO week."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from workspace.efficiency import storage
from workspace.efficiency.tracker import now_ms


def boundary(day, timezone):
    return int(datetime.combine(day, time.min, tzinfo=timezone).timestamp() * 1000)


def date_value(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        parsed = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError("Дата должна иметь формат YYYY-MM-DD") from None
    if parsed.isoformat() != value:
        raise ValueError("Дата должна иметь формат YYYY-MM-DD")
    return parsed


def statistics(from_date=None, to_date=None, timezone_name="Europe/Moscow"):
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError("Неизвестный часовой пояс") from None
    instant = now_ms()
    today = datetime.fromtimestamp(instant / 1000, timezone).date()
    end_day = date_value(to_date, today)
    start_day = date_value(from_date, end_day - timedelta(days=6))
    if end_day < start_day or (end_day - start_day).days > 3660:
        raise ValueError("Выберите диапазон дат от 1 дня до 10 лет")
    range_start, range_end = boundary(start_day, timezone), boundary(end_day + timedelta(days=1), timezone)
    daily = {}
    day = start_day
    while day <= end_day:
        daily[day.isoformat()] = {"date": day.isoformat(), "totals": {}, "totalSeconds": 0}
        day += timedelta(days=1)
    with storage.transaction() as state:
        sessions = list(state["sessions"])
        active = state["tracker"]["activeSession"]
        if active:
            sessions.append(active)
        catalog = {}
        for session in sessions:
            catalog.update({mode["id"]: mode for mode in session["modes"]})
            for segment in session["segments"]:
                begin = max(segment["start"], range_start)
                finish = min(segment["end"] if segment["end"] is not None else instant, range_end)
                while begin < finish:
                    local_day = datetime.fromtimestamp(begin / 1000, timezone).date()
                    stop = min(finish, boundary(local_day + timedelta(days=1), timezone))
                    seconds = (stop - begin) / 1000
                    bucket = daily[local_day.isoformat()]
                    mode_id = segment["modeId"]
                    bucket["totals"][mode_id] = bucket["totals"].get(mode_id, 0) + seconds
                    begin = stop
        catalog.update({mode["id"]: mode for mode in state["tracker"]["modes"]})
    weekly, totals = {}, {}
    for row in daily.values():
        row["totals"] = {key: round(value, 3) for key, value in row["totals"].items()}
        row["totalSeconds"] = round(sum(row["totals"].values()), 3)
        day = date.fromisoformat(row["date"])
        monday = day - timedelta(days=day.weekday())
        key = monday.isoformat()
        week = weekly.setdefault(key, {"startDate": key, "endDate": (monday + timedelta(days=6)).isoformat(), "totals": {}, "totalSeconds": 0})
        for mode_id, seconds in row["totals"].items():
            week["totals"][mode_id] = week["totals"].get(mode_id, 0) + seconds
            totals[mode_id] = totals.get(mode_id, 0) + seconds
        week["totalSeconds"] += row["totalSeconds"]
    for row in weekly.values():
        row["totals"] = {key: round(value, 3) for key, value in row["totals"].items()}
        row["totalSeconds"] = round(row["totalSeconds"], 3)
    totals = {key: round(value, 3) for key, value in totals.items()}
    return {"from": start_day.isoformat(), "to": end_day.isoformat(), "timezone": timezone_name,
            "modes": list(catalog.values()), "daily": list(daily.values()), "weekly": list(weekly.values()),
            "totals": totals, "totalSeconds": round(sum(totals.values()), 3)}
