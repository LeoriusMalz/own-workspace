"""Frontend assets, SPA routes, health check, and HTTP security headers."""

from __future__ import annotations

from flask import Blueprint, jsonify, send_from_directory

from workspace.config import ALLOWED_YT_CLUSTERS, STATIC_DIR


bp = Blueprint("web", __name__)


@bp.app_errorhandler(413)
def payload_too_large(_: Exception):
    return jsonify({"error": "Файл или запрос слишком большой"}), 413


@bp.after_app_request
def security_headers(response):
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@bp.get("/api/health")
def health():
    return jsonify({"ok": True, "ytClusters": sorted(ALLOWED_YT_CLUSTERS)})


@bp.get("/assets/<path:filename>")
def assets(filename: str):
    return send_from_directory(STATIC_DIR / "assets", filename)


@bp.get("/styles.css")
def styles():
    return send_from_directory(STATIC_DIR, "styles.css")


@bp.get("/yt-creator.css")
def yt_creator_styles():
    return send_from_directory(STATIC_DIR, "yt-creator.css")


@bp.get("/yt-manager.css")
def yt_manager_styles():
    return send_from_directory(STATIC_DIR, "yt-manager.css")


@bp.get("/yt-mutator.css")
def yt_mutator_styles():
    return send_from_directory(STATIC_DIR, "yt-mutator.css")


@bp.get("/app.js")
def javascript():
    return send_from_directory(STATIC_DIR, "app.js")


@bp.get("/yt-creator.js")
def yt_creator_javascript():
    return send_from_directory(STATIC_DIR, "yt-creator.js")


@bp.get("/yt-manager.js")
def yt_manager_javascript():
    return send_from_directory(STATIC_DIR, "yt-manager.js")


@bp.get("/yt-mutator.js")
def yt_mutator_javascript():
    return send_from_directory(STATIC_DIR, "yt-mutator.js")


@bp.get("/jira-creator.js")
def jira_creator_javascript():
    return send_from_directory(STATIC_DIR, "jira-creator.js")


@bp.get("/jira-creator.css")
def jira_creator_styles():
    return send_from_directory(STATIC_DIR, "jira-creator.css")


@bp.get("/")
@bp.get("/tools")
@bp.get("/notes")
@bp.get("/tools/<path:_>")
@bp.get("/notes/<path:_>")
def spa(_: str | None = None):
    return send_from_directory(STATIC_DIR, "index.html")
