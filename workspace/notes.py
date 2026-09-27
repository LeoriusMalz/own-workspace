"""File-backed notes, drafts, publication, and image uploads."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from workspace.common import LOGGER, json_body
from workspace.config import (
    ALLOWED_IMAGE_EXTENSIONS,
    ARTICLES_DIR,
    DRAFTS_DIR,
    MAX_NOTE_BYTES,
    MAX_UPLOAD_BYTES,
    SLUG_RE,
    UPLOADS_DIR,
)


bp = Blueprint("notes", __name__)


RU_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_directories() -> None:
    for directory in (DRAFTS_DIR, ARTICLES_DIR, UPLOADS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def make_slug(value: str, fallback: str = "note") -> str:
    transliterated = "".join(RU_TO_LATIN.get(char, char) for char in value.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    return slug[:90].rstrip("-") or fallback


def unique_slug(directory: Path, base_slug: str, *, except_slug: str | None = None) -> str:
    candidate = base_slug
    suffix = 2
    while candidate != except_slug and (directory / f"{candidate}.json").exists():
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


def note_path(kind: str, slug: str) -> Path:
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("Некорректный slug заметки")
    if kind == "drafts":
        return DRAFTS_DIR / f"{slug}.json"
    if kind == "articles":
        return ARTICLES_DIR / f"{slug}.json"
    raise ValueError("Неизвестный тип заметки")


def read_json_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} должен содержать JSON-объект")
    return value


def write_json_file(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    if len(payload.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValueError("Заметка слишком большая")

    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def note_summary(note: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "slug": note.get("slug"),
        "title": note.get("title") or "Без названия",
        "kind": note.get("kind"),
        "updatedAt": note.get("updatedAt"),
        "createdAt": note.get("createdAt"),
        "sourceArticleSlug": note.get("sourceArticleSlug"),
    }


def list_notes(directory: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            result.append(note_summary(read_json_file(path)))
        except Exception:
            LOGGER.exception("Failed to read note file %s", path)
    result.sort(key=lambda note: str(note.get("updatedAt") or ""), reverse=True)
    return result


@bp.get("/api/notes")
def api_list_notes():
    return jsonify({
        "drafts": list_notes(DRAFTS_DIR),
        "articles": list_notes(ARTICLES_DIR),
    })


@bp.post("/api/notes/drafts")
def api_create_draft():
    now = utc_now()
    slug = unique_slug(
        DRAFTS_DIR,
        f"draft-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}",
    )
    note = {
        "slug": slug,
        "title": "Новый черновик",
        "kind": "draft",
        "content": "<p></p>",
        "createdAt": now,
        "updatedAt": now,
        "sourceArticleSlug": None,
    }
    write_json_file(note_path("drafts", slug), note)
    return jsonify({"result": note}), 201


@bp.get("/api/notes/<kind>/<slug>")
def api_get_note(kind: str, slug: str):
    try:
        path = note_path(kind, slug)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if not path.exists():
        return jsonify({"error": "Заметка не найдена"}), 404
    return jsonify({"result": read_json_file(path)})


@bp.put("/api/notes/drafts/<slug>")
def api_update_draft(slug: str):
    try:
        path = note_path("drafts", slug)
        if not path.exists():
            return jsonify({"error": "Черновик не найден"}), 404
        body = json_body()
        note = read_json_file(path)
        title = str(body.get("title", note.get("title", "Новый черновик"))).strip()
        content = body.get("content", note.get("content", "<p></p>"))
        if not isinstance(content, str):
            raise ValueError("Содержимое заметки должно быть строкой")
        note.update({
            "title": title[:200] or "Новый черновик",
            "content": content,
            "updatedAt": utc_now(),
        })
        write_json_file(path, note)
        return jsonify({"result": note_summary(note)})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@bp.post("/api/notes/articles/<slug>/edit")
def api_edit_article(slug: str):
    try:
        article_path = note_path("articles", slug)
        if not article_path.exists():
            return jsonify({"error": "Статья не найдена"}), 404
        for existing_path in DRAFTS_DIR.glob("*.json"):
            existing = read_json_file(existing_path)
            if existing.get("sourceArticleSlug") == slug:
                return jsonify({"result": existing})
        article = read_json_file(article_path)
        draft_slug = unique_slug(DRAFTS_DIR, f"{slug}-edit")
        now = utc_now()
        draft = {
            **article,
            "slug": draft_slug,
            "kind": "draft",
            "createdAt": now,
            "updatedAt": now,
            "sourceArticleSlug": slug,
        }
        write_json_file(note_path("drafts", draft_slug), draft)
        return jsonify({"result": draft}), 201
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@bp.post("/api/notes/drafts/<slug>/publish")
def api_publish_draft(slug: str):
    try:
        draft_path = note_path("drafts", slug)
        if not draft_path.exists():
            return jsonify({"error": "Черновик не найден"}), 404
        body = json_body()
        title = str(body.get("title", "")).strip()
        if not title:
            raise ValueError("Введите название статьи")

        draft = read_json_file(draft_path)
        source_slug = draft.get("sourceArticleSlug")
        article_slug = (
            str(source_slug)
            if source_slug
            else unique_slug(ARTICLES_DIR, make_slug(title))
        )
        article_path = note_path("articles", article_slug)
        now = utc_now()
        article = {
            **draft,
            "slug": article_slug,
            "title": title[:200],
            "kind": "article",
            "updatedAt": now,
            "publishedAt": draft.get("publishedAt") or now,
            "sourceArticleSlug": None,
        }
        write_json_file(article_path, article)
        draft_path.unlink()
        return jsonify({"result": article})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@bp.post("/api/notes/drafts/<slug>/cancel")
def api_cancel_edit(slug: str):
    try:
        draft_path = note_path("drafts", slug)
        if not draft_path.exists():
            return jsonify({"error": "Черновик не найден"}), 404
        draft = read_json_file(draft_path)
        source_slug = draft.get("sourceArticleSlug")
        if not source_slug:
            raise ValueError("Это обычный черновик — его можно удалить через кнопку «Удалить»")
        draft_path.unlink()
        return jsonify({"result": {"articleSlug": source_slug}})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@bp.delete("/api/notes/<kind>/<slug>")
def api_delete_note(kind: str, slug: str):
    try:
        path = note_path(kind, slug)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if not path.exists():
        return jsonify({"error": "Заметка не найдена"}), 404
    path.unlink()
    return jsonify({"result": {"deleted": True}})


@bp.post("/api/notes/uploads")
def api_upload_image():
    file = request.files.get("image")
    if file is None or not file.filename:
        return jsonify({"error": "Изображение не выбрано"}), 400

    original_name = secure_filename(file.filename)
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({"error": "Поддерживаются PNG, JPG, GIF, WEBP и SVG"}), 400

    mime_type = file.mimetype or mimetypes.guess_type(original_name)[0] or ""
    if not mime_type.startswith("image/"):
        return jsonify({"error": "Файл не является изображением"}), 400

    filename = f"{uuid.uuid4().hex}{extension}"
    target = UPLOADS_DIR / filename
    file.save(target)
    if target.stat().st_size > MAX_UPLOAD_BYTES:
        target.unlink(missing_ok=True)
        return jsonify({"error": "Изображение слишком большое"}), 413
    return jsonify({"result": {"url": f"/notes-media/{filename}"}}), 201


@bp.get("/notes-media/<filename>")
def notes_media(filename: str):
    return send_from_directory(UPLOADS_DIR, secure_filename(filename))
