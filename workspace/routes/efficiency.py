"""HTTP routes for Pomodoro, server media, sessions, and calendar statistics."""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, Request, jsonify, request, send_file

from workspace.common import json_body
from workspace.efficiency import media, stats, timer, tracker
from workspace.efficiency.validation import ConflictError

bp = Blueprint("efficiency", __name__, url_prefix="/api/efficiency")


class EfficiencyRequest(Request):
    """Per-route upload allowance, also compatible with Flask 3.0's read-only property."""

    @property
    def max_content_length(self):
        if self.method == "POST" and self.path == "/api/efficiency/media":
            return media.MAX_MEDIA_BYTES + 1024 * 1024
        return super().max_content_length


def endpoint(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return jsonify({"result": function(*args, **kwargs)})
        except ConflictError as error:
            return jsonify({"error": str(error), "code": "conflict"}), 409
        except FileNotFoundError as error:
            return jsonify({"error": str(error)}), 404
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except OSError:
            return jsonify({"error": "Не удалось прочитать или сохранить данные в data/efficiency"}), 500
    return wrapped


@bp.get("/timer")
@endpoint
def timer_get():
    return timer.get_timer()


@bp.put("/timer")
@endpoint
def timer_put():
    return timer.put_timer(json_body())


@bp.get("/media")
@endpoint
def media_list():
    return media.list_media()


@bp.post("/media")
@endpoint
def media_upload():
    return media.upload(request.files.get("file"))


@bp.delete("/media/<media_id>")
@endpoint
def media_delete(media_id):
    return media.delete(media_id)


@bp.get("/media/<media_id>/content")
def media_content(media_id):
    try:
        path, item = media.content(media_id)
    except (FileNotFoundError, ValueError):
        return jsonify({"error": "Файл не найден"}), 404
    response = send_file(path, mimetype=item["mime"], download_name=item["name"], conditional=True)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@bp.get("/tracker")
@endpoint
def tracker_get():
    return tracker.get_tracker()


@bp.put("/modes")
@endpoint
def modes_put():
    return tracker.put_modes(json_body())


@bp.post("/tracker/action")
@endpoint
def tracker_action():
    return tracker.action(json_body())


@bp.get("/sessions")
@endpoint
def sessions_list():
    return tracker.list_sessions()


@bp.get("/sessions/<session_id>")
@endpoint
def session_get(session_id):
    return tracker.get_session(session_id)


@bp.put("/sessions/<session_id>")
@endpoint
def session_put(session_id):
    return tracker.edit_session(session_id, json_body())


@bp.get("/stats")
@endpoint
def statistics():
    return stats.statistics(request.args.get("from"), request.args.get("to"), request.args.get("timezone", "Europe/Moscow"))
