"""Local media uploads with bounded streaming, safe names, and explicit formats."""

from __future__ import annotations

import copy
import os
import uuid
from pathlib import Path

from werkzeug.exceptions import RequestEntityTooLarge

from workspace.efficiency import storage
from workspace.efficiency.validation import ConflictError, identifier

MAX_MEDIA_BYTES = 200 * 1024 * 1024
FORMATS = {
    ".mp3": ("audio", "audio/mpeg"), ".wav": ("audio", "audio/wav"),
    ".ogg": ("audio", "audio/ogg"), ".oga": ("audio", "audio/ogg"),
    ".opus": ("audio", "audio/ogg"), ".m4a": ("audio", "audio/mp4"),
    ".aac": ("audio", "audio/aac"), ".flac": ("audio", "audio/flac"),
    ".jpg": ("image", "image/jpeg"), ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"), ".gif": ("image", "image/gif"),
    ".webp": ("image", "image/webp"), ".mp4": ("video", "video/mp4"),
    ".m4v": ("video", "video/mp4"), ".webm": ("video", "video/webm"),
    ".mov": ("video", "video/quicktime"),
}


def public(item):
    return {key: value for key, value in item.items() if key != "filename"}


def list_media():
    with storage.transaction() as state:
        return [public(item) for item in state["media"]]


def valid_signature(extension, head):
    if extension in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if extension in (".wav", ".webp"):
        return head.startswith(b"RIFF") and head[8:12] == (b"WAVE" if extension == ".wav" else b"WEBP")
    if extension in (".ogg", ".oga", ".opus"):
        return head.startswith(b"OggS")
    if extension == ".flac":
        return head.startswith(b"fLaC")
    if extension in (".m4a", ".mp4", ".m4v", ".mov"):
        return len(head) >= 12 and head[4:8] == b"ftyp"
    if extension == ".webm":
        return head.startswith(b"\x1aE\xdf\xa3")
    if extension == ".mp3":
        return head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 255 and head[1] & 224 == 224)
    if extension == ".aac":
        return len(head) >= 2 and head[0] == 255 and head[1] & 240 == 240
    return False


def upload(file):
    if file is None or not file.filename:
        raise ValueError("Выберите аудио, изображение или видео")
    original = str(file.filename).replace("\\", "/").rsplit("/", 1)[-1]
    original = "".join(char for char in original if ord(char) >= 32).strip()
    if not original or len(original) > 200:
        raise ValueError("Имя файла должно содержать от 1 до 200 символов")
    extension = Path(original).suffix.lower()
    if extension not in FORMATS:
        raise ValueError("Поддерживаются MP3, WAV, OGG, OPUS, M4A, AAC, FLAC, JPG, PNG, GIF, WEBP, MP4, M4V, WEBM и MOV")
    kind, mime = FORMATS[extension]
    declared = file.mimetype or "application/octet-stream"
    if declared.startswith("audio/") and extension in (".mp4", ".webm"):
        kind, mime = "audio", "audio/mp4" if extension == ".mp4" else "audio/webm"
    if declared != "application/octet-stream" and not declared.startswith(kind + "/"):
        raise ValueError("Тип загружаемого файла не соответствует его расширению")
    head = file.stream.read(64)
    if not valid_signature(extension, head):
        raise ValueError("Содержимое файла не соответствует выбранному формату")
    media_id = uuid.uuid4().hex
    filename = media_id + extension
    directory = storage.DATA_DIR / "media"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    temporary = directory / (filename + ".part")
    size = len(head)
    try:
        with temporary.open("xb") as output:
            output.write(head)
            while chunk := file.stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_MEDIA_BYTES:
                    raise RequestEntityTooLarge()
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        item = {"id": media_id, "name": original, "kind": kind, "url": f"/api/efficiency/media/{media_id}/content",
                "mime": mime, "size": size, "filename": filename}
        with storage.transaction(write=True) as state:
            state["media"].append(item)
        return public(item)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def content(media_id):
    identifier(media_id, "Медиафайл")
    with storage.transaction() as state:
        item = next((item for item in state["media"] if item["id"] == media_id), None)
        if item is None:
            raise FileNotFoundError("Файл не найден")
        path = storage.DATA_DIR / "media" / item["filename"]
        if not path.is_file():
            raise FileNotFoundError("Файл отсутствует на сервере")
        return path, copy.deepcopy(item)


def delete(media_id):
    identifier(media_id, "Медиафайл")
    with storage.transaction(write=True) as state:
        item = next((item for item in state["media"] if item["id"] == media_id), None)
        if item is None:
            raise FileNotFoundError("Файл не найден")
        timer = state["timer"]
        refs = ("workAudio", "restAudio", "workBackground", "restBackground")
        if any(settings.get(key) == media_id for settings in (timer["settings"], timer["pomodoro"]["config"]) for key in refs):
            raise ConflictError("Файл выбран в настройках таймера. Сначала замените его и остановите старый таймер.")
        state["media"] = [existing for existing in state["media"] if existing["id"] != media_id]
        filename = item["filename"]
    (storage.DATA_DIR / "media" / filename).unlink(missing_ok=True)
    return {"deleted": True}
