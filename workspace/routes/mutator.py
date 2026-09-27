"""HTTP endpoints for YT Mutator."""

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
from workspace.yt.mutator_data import (
    mutator_apply_data_changes,
    mutator_insert_rows,
    mutator_search_rows,
    mutator_validate_insert_rows,
)
from workspace.yt.mutator_schema import (
    mutator_apply_schema,
    mutator_schema_plan,
    mutator_table_info,
)


bp = Blueprint("routes_mutator", __name__)


@bp.post("/api/yt/mutator/inspect")
def api_mutator_inspect():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        return jsonify({"result": mutator_table_info(path, token, cluster)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator inspection failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@bp.post("/api/yt/mutator/schema/plan")
def api_mutator_schema_plan():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        return jsonify({"result": mutator_schema_plan(info, body.get("change"), token)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "schema_incompatible"}), 409
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator schema plan failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@bp.post("/api/yt/mutator/schema/apply")
def api_mutator_schema_apply():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Изменение схемы не подтверждено")
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        if str(body.get("expectedSchemaHash") or "") != info["schemaHash"]:
            raise YTManagerConflictError("Схема таблицы изменилась после загрузки. Обновите данные.")
        plan = mutator_schema_plan(info, body.get("change"), token)
        return jsonify({"result": mutator_apply_schema(info, plan, token)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "schema_incompatible"}), 409
    except YTManagerPartialError as error:
        LOGGER.exception("YT Mutator schema apply stopped after partial success")
        return jsonify({
            "error": f"Схема изменена частично: {error}",
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
        LOGGER.exception("YT Mutator schema apply failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@bp.post("/api/yt/mutator/data/search")
def api_mutator_data_search():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        return jsonify({"result": mutator_search_rows(info, token, body)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "operation_unavailable"}), 409
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator search failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@bp.post("/api/yt/mutator/data/validate-inserts")
def api_mutator_validate_inserts():
    try:
        body = json_body()
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        validation = mutator_validate_insert_rows(info, token, body.get("rows"))
        validation.pop("parsedRows", None)
        return jsonify({"result": validation})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "operation_unavailable"}), 409
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator insert validation failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@bp.post("/api/yt/mutator/data/apply")
def api_mutator_data_apply():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Изменения строк не подтверждены")
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        if str(body.get("expectedSchemaHash") or "") != info["schemaHash"]:
            raise YTManagerConflictError("Схема таблицы изменилась. Обновите результаты поиска.")
        return jsonify({"result": mutator_apply_data_changes(info, token, body)})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "state_conflict"}), 409
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator data apply failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@bp.post("/api/yt/mutator/data/insert")
def api_mutator_data_insert():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Добавление строк не подтверждено")
        path = normalize_table_path(body.get("path"))
        cluster = normalize_cluster(body.get("cluster"))
        token = request.headers.get("X-YT-Token", "").strip()
        info = mutator_table_info(path, token, cluster)
        if str(body.get("expectedSchemaHash") or "") != info["schemaHash"]:
            raise YTManagerConflictError("Схема таблицы изменилась. Перезагрузите форму.")
        return jsonify({"result": mutator_insert_rows(info, token, body.get("rows"))})
    except FileNotFoundError:
        return jsonify({"error": "Таблица не найдена", "code": "node_not_found"}), 404
    except YTManagerConflictError as error:
        return jsonify({"error": str(error), "code": "state_conflict"}), 409
    except YTAccessDeniedError:
        return jsonify({"error": "Доступ ограничен для данного токена", "code": "access_denied"}), 403
    except PermissionError as error:
        return jsonify({"error": str(error), "code": "token_required"}), 401
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_request"}), 400
    except Exception as error:
        LOGGER.exception("YT Mutator insert failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502
