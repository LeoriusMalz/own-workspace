"""HTTP endpoints for YT Manager."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from workspace.common import LOGGER, json_body
from workspace.yt.common import (
    YTAccessDeniedError,
    YTManagerConflictError,
    YTManagerPartialError,
    normalize_cluster,
    normalize_table_path,
    to_jsonable,
)
from workspace.yt.manager import (
    manager_delete,
    manager_destination_action,
    manager_inspect,
    manager_mount_action,
    manager_require_source,
    manager_update_attributes,
    manager_update_global_tracker,
    manager_update_replica,
    manager_validate_destination,
)


bp = Blueprint("routes_manager", __name__)


@bp.post("/api/yt/manager/inspect")
def api_manager_inspect():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        return jsonify({"result": manager_inspect(path, token, cluster)})
    except FileNotFoundError:
        return jsonify({"error": "YT-нода не найдена", "code": "node_not_found"}), 404
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Manager inspection failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@bp.post("/api/yt/manager/validate-destination")
def api_manager_validate_destination():
    try:
        body = json_body()
        source_path = normalize_table_path(body.get("sourcePath"))
        destination_path = normalize_table_path(body.get("destinationPath"))
        cluster = normalize_cluster(body.get("cluster"))
        operation = str(body.get("operation") or "")
        token = request.headers.get("X-YT-Token", "").strip()
        return jsonify({
            "result": manager_validate_destination(
                source_path, destination_path, token, cluster, operation,
            ),
        })
    except FileNotFoundError:
        return jsonify({"error": "Исходная YT-нода не найдена", "code": "node_not_found"}), 404
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Manager destination validation failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@bp.post("/api/yt/manager/action")
def api_manager_action():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Операция не подтверждена")
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        action = str(body.get("action") or "")
        token = request.headers.get("X-YT-Token", "").strip()
        info = manager_require_source(path, token, cluster, allow_link=action == "delete")

        if action == "update_attributes":
            completed = manager_update_attributes(path, token, cluster, body.get("changes"))
        elif action in {"link", "copy", "move"}:
            destination_path = normalize_table_path(body.get("destinationPath"))
            completed = manager_destination_action(info, token, action, destination_path)
        elif action == "mount":
            completed = manager_mount_action(path, token, cluster, True)
        elif action == "unmount":
            completed = manager_mount_action(path, token, cluster, False)
        elif action == "update_replica":
            completed = manager_update_replica(
                info,
                token,
                str(body.get("replicaId") or ""),
                body.get("state"),
                body.get("mode"),
                body.get("trackerEnabled"),
            )
        elif action == "update_global_tracker":
            completed = manager_update_global_tracker(info, token, body.get("enabled"))
        elif action == "delete":
            if body.get("doubleConfirmed") is not True or body.get("confirmationText") != path:
                raise ValueError("Для удаления нужно повторно ввести точный путь YT-ноды")
            completed = manager_delete(info, token)
        else:
            raise ValueError("Неизвестная операция YT Manager")

        return jsonify({"result": {"completed": to_jsonable(completed)}})
    except FileNotFoundError:
        return jsonify({"error": "YT-нода больше не существует", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "state_conflict"}), 409
    except YTManagerPartialError as error:
        LOGGER.exception("YT Manager action stopped after partial success")
        return jsonify({
            "error": f"Операция выполнена частично: {error}",
            "code": "partial_action",
            "completed": to_jsonable(error.completed),
        }), 502
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Manager action failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502
