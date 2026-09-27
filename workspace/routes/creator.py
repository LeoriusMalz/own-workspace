"""HTTP endpoints for YT Creator."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from workspace.common import LOGGER, json_body
from workspace.yt import creator
from workspace.yt.common import YTAccessDeniedError, YTCreationError
from workspace.yt.creator import (
    create_directory_chain,
    create_yt_table,
    inspect_directory_chain,
    normalize_creator_config,
    normalize_directory_config,
    parse_schema_text,
    schema_conversion_result,
)


bp = Blueprint("routes_creator", __name__)


@bp.post("/api/yt/creator/schema/convert")
def api_creator_schema_convert():
    try:
        body = json_body()
        schema = parse_schema_text(body.get("text"), body.get("format"))
        return jsonify({"result": schema_conversion_result(schema)})
    except ValueError as error:
        return jsonify({"error": str(error), "code": "invalid_schema"}), 400
    except Exception as error:
        LOGGER.exception("YT schema conversion failed")
        return jsonify({"error": str(error), "code": "schema_conversion_error"}), 500


@bp.post("/api/yt/creator/validate")
def api_creator_validate():
    try:
        body = json_body()
        config = normalize_creator_config(body, require_schema_rules=False)
        token = request.headers.get("X-YT-Token", "").strip()
        result = creator.validate_creator_destinations(config, token)
        return jsonify({"result": result})
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
        LOGGER.exception("YT creator path validation failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@bp.post("/api/yt/creator/create")
def api_creator_create():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Создание таблицы не подтверждено")
        config = normalize_creator_config(body, require_schema_rules=True)
        token = request.headers.get("X-YT-Token", "").strip()
        validation = creator.validate_creator_destinations(config, token)
        if not validation["valid"]:
            return jsonify({
                "error": "Создание невозможно: путь изменился или таблица уже существует",
                "code": "destination_invalid",
                "details": validation,
            }), 409
        created = create_yt_table(config, token)
        return jsonify({"result": {"created": created}}), 201
    except YTCreationError as error:
        LOGGER.exception("YT table creation stopped after partial success")
        return jsonify({
            "error": (
                "Создание завершилось частично. Не удаляйте созданные объекты автоматически; "
                f"проверьте список и исходную ошибку: {error}"
            ),
            "code": "partial_creation",
            "created": error.created,
        }), 502
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
        LOGGER.exception("YT table creation failed")
        error_text = str(error)
        code = "already_exists" if "already exists" in error_text.lower() else "creation_failed"
        status = 409 if code == "already_exists" else 502
        return jsonify({"error": error_text, "code": code}), status


@bp.post("/api/yt/creator/directory/validate")
def api_creator_directory_validate():
    try:
        config = normalize_directory_config(json_body())
        token = request.headers.get("X-YT-Token", "").strip()
        return jsonify({"result": inspect_directory_chain(config, token)})
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
        LOGGER.exception("YT directory chain validation failed")
        return jsonify({"error": str(error), "code": "yt_error"}), 502


@bp.post("/api/yt/creator/directory/create")
def api_creator_directory_create():
    try:
        body = json_body()
        if body.get("confirmed") is not True:
            raise ValueError("Создание директорий не подтверждено")
        config = normalize_directory_config(body)
        token = request.headers.get("X-YT-Token", "").strip()
        plan = inspect_directory_chain(config, token)
        if not plan["valid"]:
            return jsonify({
                "error": plan["reason"] or "Создание директорий невозможно",
                "code": "destination_invalid",
                "details": plan,
            }), 409
        created = create_directory_chain(config, token, plan)
        return jsonify({"result": {"created": created}}), 201
    except YTCreationError as error:
        LOGGER.exception("YT directory creation stopped after partial success")
        return jsonify({
            "error": (
                "Цепочка директорий создана частично. Проверьте уже созданные пути: "
                f"{error}"
            ),
            "code": "partial_creation",
            "created": error.created,
        }), 502
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
        LOGGER.exception("YT directory creation failed")
        error_text = str(error)
        code = "already_exists" if "already exists" in error_text.lower() else "creation_failed"
        status = 409 if code == "already_exists" else 502
        return jsonify({"error": error_text, "code": code}), status
