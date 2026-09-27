"""HTTP endpoints for YT Observer."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from workspace.common import LOGGER, json_body
from workspace.yt.common import YTAccessDeniedError, normalize_cluster, normalize_table_path
from workspace.yt.observer import table_info


bp = Blueprint("routes_observer", __name__)


@bp.post("/api/yt/table-info")
def api_table_info():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        return jsonify({"result": table_info(path, token, cluster)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица или папка не найдена", "code": "node_not_found"}), 404
    except YTAccessDeniedError:
        return jsonify({
            "error": "Доступ ограничен для данного токена",
            "code": "access_denied",
        }), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT table inspection failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502
