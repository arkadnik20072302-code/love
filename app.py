import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA_DIR = ROOT / "data"
DB_FILE = DATA_DIR / "wishlist.sqlite3"
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_MEMORY_BODY_BYTES = 30 * 1024 * 1024
MAX_PHOTO_BYTES = 20 * 1024 * 1024
MAX_STORY_PHOTO_BYTES = 3 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 256 * 1024
PHOTO_PATTERN = re.compile(r"^data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)$")
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
DB_LOCK = threading.RLock()
TELEGRAM_STATUS = {
    "enabled": False,
    "running": False,
    "cloudManaged": False,
    "error": "",
}
TELEGRAM_PENDING_ACTIONS = {}
TELEGRAM_CHAT_WARNINGS = set()
TELEGRAM_KEYBOARD = {
    "keyboard": [
        [{"text": "➕ Добавить желание"}, {"text": "🔔 Напоминание"}],
        [{"text": "📋 Мои желания"}, {"text": "ℹ️ Помощь"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}
MAX_TELEGRAM_VOICE_BYTES = 20 * 1024 * 1024
MAX_EDGE_VOICE_BYTES = 5 * 1024 * 1024
MAX_TELEGRAM_VOICE_SECONDS = 180
VOICE_FUNCTION_SECRET_CONTEXT = b"wishlist-voice-function-v1"
VOICE_MODEL = None
VOICE_MODEL_LOCK = threading.Lock()
AUTH_COOKIE_NAME = "wishlist_session"
AUTH_SESSION_SECONDS = 30 * 24 * 60 * 60
AUTH_RATE_LIMIT = {}
AUTH_RATE_LIMIT_LOCK = threading.Lock()


class SupabaseError(RuntimeError):
    pass


class TelegramError(RuntimeError):
    pass


def auth_config():
    password = os.environ.get("APP_PASSWORD", "")
    signing_secret = os.environ.get("APP_SESSION_SECRET", "")
    if bool(password) != bool(signing_secret):
        raise RuntimeError(
            "Для входа задайте одновременно APP_PASSWORD и APP_SESSION_SECRET."
        )
    if password and len(password) < 12:
        raise RuntimeError("APP_PASSWORD должен содержать не менее 12 символов.")
    if password and len(signing_secret) < 32:
        raise RuntimeError("APP_SESSION_SECRET должен содержать не менее 32 символов.")
    if os.environ.get("RENDER") and not password:
        raise RuntimeError(
            "В настройках Render обязательно задайте APP_PASSWORD и APP_SESSION_SECRET."
        )
    return password, signing_secret


def create_auth_cookie(signing_secret, now=None):
    now = int(time.time() if now is None else now)
    expiry = now + AUTH_SESSION_SECONDS
    nonce = secrets.token_urlsafe(24)
    payload = f"{expiry}.{nonce}"
    signature = hmac.new(
        signing_secret.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}", expiry


def validate_auth_cookie(cookie_value, signing_secret, now=None):
    if not isinstance(cookie_value, str) or len(cookie_value) > 256:
        return False
    parts = cookie_value.split(".")
    if len(parts) != 3 or not parts[0].isdigit():
        return False
    expiry, nonce, signature = parts
    if not nonce or not re.fullmatch(r"[A-Za-z0-9_-]+", nonce):
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        return False
    current_time = int(time.time() if now is None else now)
    if int(expiry) <= current_time or int(expiry) > current_time + AUTH_SESSION_SECONDS:
        return False
    payload = f"{expiry}.{nonce}"
    expected = hmac.new(
        signing_secret.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def allow_login_attempt(client_id, now=None):
    now = time.time() if now is None else now
    with AUTH_RATE_LIMIT_LOCK:
        if len(AUTH_RATE_LIMIT) > 10_000:
            expired = [
                key for key, (started, _count) in AUTH_RATE_LIMIT.items()
                if now - started >= 15 * 60
            ]
            for key in expired:
                AUTH_RATE_LIMIT.pop(key, None)
        started, count = AUTH_RATE_LIMIT.get(client_id, (now, 0))
        if now - started >= 15 * 60:
            started, count = now, 0
        if count >= 10:
            AUTH_RATE_LIMIT[client_id] = (started, count)
            return False
        AUTH_RATE_LIMIT[client_id] = (started, count + 1)
        return True


def supabase_config():
    project_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    service_key = (
        os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    if bool(project_url) != bool(service_key):
        raise SupabaseError(
            "Для Supabase задайте SUPABASE_URL и SUPABASE_SECRET_KEY "
            "(или legacy SUPABASE_SERVICE_ROLE_KEY)."
        )
    if not project_url:
        return None
    parsed = urllib.parse.urlparse(project_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SupabaseError("SUPABASE_URL должен быть HTTPS-адресом проекта.")
    return project_url, service_key


def supabase_request(method, resource, payload=None, prefer=None):
    config = supabase_config()
    if config is None:
        raise SupabaseError("Supabase не настроен.")
    project_url, service_key = config
    headers = {
        "apikey": service_key,
        "Accept": "application/json",
    }
    if not service_key.startswith(("sb_secret_", "sb_publishable_")):
        headers["Authorization"] = f"Bearer {service_key}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{project_url}/rest/v1/{resource}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        response_headers = exc.headers or {}
        try:
            parsed_detail = json.loads(detail)
            if isinstance(parsed_detail, dict):
                message = parsed_detail.get("message") or parsed_detail.get("hint")
                if message:
                    detail = message
        except json.JSONDecodeError:
            pass
        if service_key:
            detail = detail.replace(service_key, "[redacted]")
        if not detail.strip():
            detail = f"ответ API пустой ({exc.reason or 'причина не указана'})"
        request_id = (
            response_headers.get("sb-request-id")
            or response_headers.get("x-request-id")
            or response_headers.get("cf-ray")
        )
        request_note = f", request id {request_id}" if request_id else ""
        content_type = response_headers.get("Content-Type")
        content_note = f", content-type {content_type}" if content_type else ""
        safe_url = urllib.parse.urlparse(request.full_url)
        raise SupabaseError(
            f"Supabase вернул HTTP {exc.code} для {exc.filename or safe_url.netloc + safe_url.path}"
            f"{request_note}{content_note}: {detail[:500]}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SupabaseError(f"Не удалось подключиться к Supabase: {exc}") from exc
    if not response_body:
        return []
    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise SupabaseError("Supabase вернул некорректный JSON.") from exc
    if not isinstance(result, list):
        raise SupabaseError("Supabase вернул неожиданный формат данных.")
    return result


def supabase_storage_request(method, resource, body=None, content_type=None):
    config = supabase_config()
    if config is None:
        raise SupabaseError("Supabase не настроен.")
    project_url, service_key = config
    headers = {
        "apikey": service_key,
        "Accept": "application/json",
    }
    if not service_key.startswith(("sb_secret_", "sb_publishable_")):
        headers["Authorization"] = f"Bearer {service_key}"
    if content_type:
        headers["Content-Type"] = content_type
    payload = body
    if isinstance(body, dict):
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{project_url}/storage/v1/{resource}",
        data=payload,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", "replace")
        if service_key:
            detail = detail.replace(service_key, "[redacted]")
        raise SupabaseError(
            f"Supabase Storage вернул HTTP {exc.code}: {detail[:300] or exc.reason}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SupabaseError(f"Не удалось подключиться к Supabase Storage: {exc}") from exc
    if not response_body:
        return {}
    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise SupabaseError("Supabase Storage вернул некорректный JSON.") from exc
    if not isinstance(result, dict):
        raise SupabaseError("Supabase Storage вернул неожиданный формат данных.")
    return result


def supabase_memory_photo_url(photo_path):
    result = supabase_storage_request(
        "POST",
        f"object/sign/memories/{urllib.parse.quote(photo_path, safe='/')}",
        {"expiresIn": 3600},
    )
    signed_url = result.get("signedURL")
    if not isinstance(signed_url, str) or not signed_url:
        raise SupabaseError("Supabase Storage не вернул ссылку на фотографию.")
    project_url, _service_key = supabase_config()
    if signed_url.startswith("https://"):
        return signed_url
    if signed_url.startswith("/storage/v1/"):
        return f"{project_url}{signed_url}"
    return f"{project_url}/storage/v1{signed_url}"


def using_supabase():
    return supabase_config() is not None


def connect_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_FILE, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def database():
    connection = connect_db()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_db():
    with DB_LOCK, database() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                together_since TEXT NOT NULL DEFAULT '',
                demob_date TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS wishes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'planned',
                created_at TEXT NOT NULL,
                planned_for TEXT NOT NULL DEFAULT '',
                planned_time TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT '',
                completed_time TEXT NOT NULL DEFAULT '',
                photo TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0,
                sent_to TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                memory_date TEXT NOT NULL,
                image_data TEXT NOT NULL,
                thought_her TEXT NOT NULL DEFAULT '',
                thought_him TEXT NOT NULL DEFAULT '',
                story_title TEXT NOT NULL DEFAULT '',
                story_text TEXT NOT NULL DEFAULT '',
                thumbnail_data TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stories (
                id TEXT PRIMARY KEY,
                story_date TEXT NOT NULL,
                title TEXT NOT NULL,
                story_text TEXT NOT NULL,
                image_data TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO settings (id) VALUES (1);
            """
        )
        wish_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(wishes)").fetchall()
        }
        if "planned_time" not in wish_columns:
            db.execute("ALTER TABLE wishes ADD COLUMN planned_time TEXT NOT NULL DEFAULT ''")
        if "completed_time" not in wish_columns:
            db.execute("ALTER TABLE wishes ADD COLUMN completed_time TEXT NOT NULL DEFAULT ''")
        memory_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(memories)").fetchall()
        }
        if "story_title" not in memory_columns:
            db.execute("ALTER TABLE memories ADD COLUMN story_title TEXT NOT NULL DEFAULT ''")
        if "story_text" not in memory_columns:
            db.execute("ALTER TABLE memories ADD COLUMN story_text TEXT NOT NULL DEFAULT ''")
        if "thumbnail_data" not in memory_columns:
            db.execute("ALTER TABLE memories ADD COLUMN thumbnail_data TEXT NOT NULL DEFAULT ''")
        db.execute(
            "INSERT OR IGNORE INTO stories "
            "(id, story_date, title, story_text, image_data, created_at) "
            "SELECT 'legacy-' || id, memory_date, "
            "COALESCE(NULLIF(trim(story_title), ''), 'История одного момента'), "
            "COALESCE(NULLIF(trim(story_text), ''), 'Эта фотография тоже хранит свою историю.'), "
            "image_data, created_at FROM memories "
            "WHERE trim(story_title) <> '' OR trim(story_text) <> ''"
        )
        if db.execute("SELECT COUNT(*) FROM wishes").fetchone()[0] == 0:
            today = datetime.date.today().isoformat()
            starter_wishes = [
                ("Устроить спонтанную поездку на выходные", "Путешествия"),
                ("Встретить рассвет в красивом месте", "Впечатления"),
                ("Посмотреть сериал, который давно откладывали", "Уютные вечера"),
                ("Съездить в горы и устроить пикник", "Путешествия"),
                ("Приготовить вместе новое блюдо", "Уютные вечера"),
                ("Сделать альбом наших совместных фото", "Впечатления"),
            ]
            db.executemany(
                "INSERT INTO wishes (id, title, category, created_at) VALUES (?, ?, ?, ?)",
                [(uuid.uuid4().hex, title, category, today) for title, category in starter_wishes],
            )


def parse_date(value, field_name):
    if value in ("", None):
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name}: укажите дату в формате ГГГГ-ММ-ДД.")
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name}: укажите корректную дату.") from exc


def parse_time(value, field_name):
    if value in ("", None):
        return ""
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", value
    ):
        raise ValueError(f"{field_name}: укажите время в формате ЧЧ:ММ.")
    return value[:5]


def validate_photo(value, max_bytes=MAX_PHOTO_BYTES):
    if value in ("", None):
        return ""
    if not isinstance(value, str):
        raise ValueError("Не удалось прочитать фотографию.")
    match = PHOTO_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("Поддерживаются фотографии JPEG, PNG и WebP.")
    if len(match.group(2)) > max_bytes * 4 // 3 + 8:
        raise ValueError(f"Фотография не должна превышать {max_bytes // (1024 * 1024)} МБ.")
    try:
        image_bytes = base64.b64decode(match.group(2), validate=True)
    except ValueError as exc:
        raise ValueError("Не удалось прочитать фотографию.") from exc
    if len(image_bytes) > max_bytes:
        raise ValueError(f"Фотография не должна превышать {max_bytes // (1024 * 1024)} МБ.")
    return value


def validate_memory_thought(value, field_name):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError(f"{field_name} должен быть не длиннее 2000 символов.")
    return value.strip()


def validate_memory_story(value, field_name, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{field_name} должен быть не длиннее {limit} символов.")
    return value.strip()


def get_memories():
    if using_supabase():
        rows = supabase_request(
            "GET",
            "memories?select=id,memory_date,photo_path,thumbnail_path,thought_her,thought_him,story_title,story_text,created_at"
            "&order=memory_date.desc,created_at.desc",
        )
        return [{
            **row,
            "photo": supabase_memory_photo_url(row.get("thumbnail_path") or row["photo_path"]),
            "photo_original": supabase_memory_photo_url(row["photo_path"]),
        } for row in rows]
    with DB_LOCK, database() as db:
        rows = db.execute(
            "SELECT id, memory_date, thumbnail_data, thought_her, thought_him, story_title, story_text, created_at "
            "FROM memories ORDER BY memory_date DESC, created_at DESC"
        ).fetchall()
    return [
        {
            "id": row["id"],
            "memory_date": row["memory_date"],
            "photo": row["thumbnail_data"] or f"/api/memories/{row['id']}/photo",
            "photo_original": f"/api/memories/{row['id']}/photo",
            "thought_her": row["thought_her"],
            "thought_him": row["thought_him"],
            "story_title": row["story_title"],
            "story_text": row["story_text"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_memory_stories():
    if using_supabase():
        return supabase_request(
            "GET",
            "memories?select=id,memory_date,photo_path,story_title,story_text,created_at"
            "&or=(story_title.neq.,story_text.neq.)"
            "&order=memory_date.desc,created_at.desc",
        )
    with DB_LOCK, database() as db:
        rows = db.execute(
            "SELECT id, memory_date, image_data, story_title, story_text, created_at FROM memories "
            "WHERE trim(story_title) <> '' OR trim(story_text) <> '' "
            "ORDER BY memory_date DESC, created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def get_stories():
    if using_supabase():
        rows = supabase_request(
            "GET",
            "stories?select=id,story_date,title,story_text,photo_path,created_at"
            "&order=story_date.desc,created_at.desc",
        )
        stories = [
            {
                "id": row["id"],
                "story_date": row["story_date"],
                "date": row["story_date"],
                "title": row["title"],
                "story_text": row["story_text"],
                "photo": supabase_memory_photo_url(row["photo_path"])
                if row.get("photo_path")
                else "",
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    else:
        with DB_LOCK, database() as db:
            rows = db.execute(
                "SELECT id, story_date, title, story_text, image_data, created_at "
                "FROM stories ORDER BY story_date DESC, created_at DESC"
            ).fetchall()
        stories = [
            {
                "id": row["id"],
                "story_date": row["story_date"],
                "date": row["story_date"],
                "title": row["title"],
                "story_text": row["story_text"],
                "photo": row["image_data"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    story_ids = {story["id"] for story in stories}
    for row in get_memory_stories():
        legacy_id = f"legacy-{row['id']}"
        if legacy_id in story_ids:
            continue
        stories.append({
            "id": legacy_id,
            "story_date": row["memory_date"],
            "date": row["memory_date"],
            "title": row["story_title"] or "История одного момента",
            "story_text": row["story_text"],
            "photo": (
                supabase_memory_photo_url(row["photo_path"])
                if using_supabase()
                else row["image_data"]
            ),
            "created_at": row["created_at"],
        })
        story_ids.add(legacy_id)
    stories.sort(
        key=lambda story: (story["date"], story.get("created_at", "")),
        reverse=True,
    )
    return stories


def create_story(story_date, title, story_text, photo=""):
    story_date = parse_date(story_date, "Дата истории")
    if not story_date:
        raise ValueError("Укажите дату истории.")
    title = validate_memory_story(title, "Название истории", 120)
    if not title:
        raise ValueError("Укажите название истории.")
    story_text = validate_memory_story(story_text, "История", 8000)
    if not story_text:
        raise ValueError("Напишите текст истории.")
    photo = validate_photo(photo, MAX_STORY_PHOTO_BYTES)
    story_id = uuid.uuid4().hex
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if using_supabase():
        photo_path = None
        if photo:
            match = PHOTO_PATTERN.fullmatch(photo)
            mime_type = match.group(1)
            extension = {"jpeg": "jpg", "png": "png", "webp": "webp"}[mime_type]
            photo_path = f"stories/{story_id}.{extension}"
            image_bytes = base64.b64decode(match.group(2), validate=True)
            supabase_storage_request(
                "POST",
                f"object/memories/{urllib.parse.quote(photo_path, safe='/')}",
                image_bytes,
                f"image/{mime_type}",
            )
        try:
            rows = supabase_request(
                "POST",
                "stories",
                {
                    "id": story_id,
                    "story_date": story_date,
                    "title": title,
                    "story_text": story_text,
                    "photo_path": photo_path,
                },
                prefer="return=representation",
            )
        except SupabaseError:
            if photo_path:
                try:
                    supabase_storage_request(
                        "DELETE",
                        "object/memories",
                        {"prefixes": [photo_path]},
                    )
                except SupabaseError as cleanup_error:
                    print(f"Не удалось удалить несвязанное фото истории: {cleanup_error}")
            raise
        if not rows:
            raise SupabaseError("Supabase не подтвердил сохранение истории.")
        return {
            **rows[0],
            "date": story_date,
            "photo": supabase_memory_photo_url(photo_path) if photo_path else "",
        }

    with DB_LOCK, database() as db:
        db.execute(
            "INSERT INTO stories (id, story_date, title, story_text, image_data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (story_id, story_date, title, story_text, photo, created_at),
        )
    return {
        "id": story_id,
        "story_date": story_date,
        "date": story_date,
        "title": title,
        "story_text": story_text,
        "photo": photo,
        "created_at": created_at,
    }


def create_memory(memory_date, photo, story_title="", story_text="", thumbnail=""):
    memory_date = parse_date(memory_date, "Дата воспоминания")
    if not memory_date:
        raise ValueError("Укажите дату воспоминания.")
    photo = validate_photo(photo)
    if not photo:
        raise ValueError("Выберите фотографию.")
    thumbnail = validate_photo(thumbnail, MAX_THUMBNAIL_BYTES) if thumbnail else ""
    story_title = validate_memory_story(story_title, "Название истории", 120)
    story_text = validate_memory_story(story_text, "История", 8000)
    memory_id = uuid.uuid4().hex
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if using_supabase():
        match = PHOTO_PATTERN.fullmatch(photo)
        mime_type = match.group(1)
        extension = {"jpeg": "jpg", "png": "png", "webp": "webp"}[mime_type]
        photo_path = f"{memory_id}.{extension}"
        thumbnail_match = PHOTO_PATTERN.fullmatch(thumbnail) if thumbnail else None
        thumbnail_extension = (
            {"jpeg": "jpg", "png": "png", "webp": "webp"}[thumbnail_match.group(1)]
            if thumbnail_match else ""
        )
        thumbnail_path = (
            f"thumbnails/{memory_id}.{thumbnail_extension}" if thumbnail_match else None
        )
        image_bytes = base64.b64decode(match.group(2), validate=True)
        supabase_storage_request(
            "POST",
            f"object/memories/{photo_path}",
            image_bytes,
            f"image/{mime_type}",
        )
        if thumbnail_path:
            thumbnail_bytes = base64.b64decode(thumbnail_match.group(2), validate=True)
            try:
                supabase_storage_request(
                    "POST",
                    f"object/memories/{thumbnail_path}",
                    thumbnail_bytes,
                    f"image/{thumbnail_match.group(1)}",
                )
            except SupabaseError:
                try:
                    supabase_storage_request(
                        "DELETE", "object/memories", {"prefixes": [photo_path]},
                    )
                except SupabaseError as cleanup_error:
                    print(f"Не удалось удалить оригинал после ошибки загрузки превью: {cleanup_error}")
                raise
        try:
            rows = supabase_request(
                "POST",
                "memories",
                {
                    "id": memory_id,
                    "memory_date": memory_date,
                    "photo_path": photo_path,
                    "thumbnail_path": thumbnail_path,
                    "thought_her": "",
                    "thought_him": "",
                    "story_title": story_title,
                    "story_text": story_text,
                },
                prefer="return=representation",
            )
        except SupabaseError:
            try:
                supabase_storage_request(
                    "DELETE",
                    "object/memories",
                    {"prefixes": [path for path in (photo_path, thumbnail_path) if path]},
                )
            except SupabaseError as cleanup_error:
                print(f"Не удалось удалить несвязанную фотографию из Storage: {cleanup_error}")
            raise
        if not rows:
            raise SupabaseError("Supabase не подтвердил сохранение воспоминания.")
        return {
            **rows[0],
            "photo": supabase_memory_photo_url(thumbnail_path or photo_path),
            "photo_original": supabase_memory_photo_url(photo_path),
        }

    with DB_LOCK, database() as db:
        db.execute(
            "INSERT INTO memories "
            "(id, memory_date, image_data, thought_her, thought_him, story_title, story_text, thumbnail_data, created_at) "
            "VALUES (?, ?, ?, '', '', ?, ?, ?, ?)",
            (memory_id, memory_date, photo, story_title, story_text, thumbnail, created_at),
        )
    return {
        "id": memory_id,
        "memory_date": memory_date,
        "photo": thumbnail or f"/api/memories/{memory_id}/photo",
        "photo_original": f"/api/memories/{memory_id}/photo",
        "thought_her": "",
        "thought_him": "",
        "story_title": story_title,
        "story_text": story_text,
        "created_at": created_at,
    }


def update_memory(memory_id, payload):
    changes = {}
    if "memory_date" in payload:
        memory_date = parse_date(payload["memory_date"], "Дата воспоминания")
        if not memory_date:
            raise ValueError("Укажите дату воспоминания.")
        changes["memory_date"] = memory_date
    for key, label in (
        ("thought_her", "Мысль"),
        ("thought_him", "Мысль"),
    ):
        if key in payload:
            changes[key] = validate_memory_thought(payload[key], label)
    if "story_title" in payload:
        changes["story_title"] = validate_memory_story(
            payload["story_title"], "Название истории", 120
        )
    if "story_text" in payload:
        changes["story_text"] = validate_memory_story(
            payload["story_text"], "История", 8000
        )
    if not changes:
        raise ValueError("Нет изменений для сохранения.")

    if using_supabase():
        rows = supabase_request(
            "PATCH",
            f"memories?id=eq.{memory_id}",
            changes,
            prefer="return=representation",
        )
        if not rows:
            return None
        row = rows[0]
        row["photo"] = supabase_memory_photo_url(
            row.get("thumbnail_path") or row["photo_path"]
        )
        row["photo_original"] = supabase_memory_photo_url(row["photo_path"])
        return row

    columns = ", ".join(f"{key} = ?" for key in changes)
    with DB_LOCK, database() as db:
        result = db.execute(
            f"UPDATE memories SET {columns} WHERE id = ?",
            [*changes.values(), memory_id],
        )
        if result.rowcount == 0:
            return None
        row = db.execute(
            "SELECT id, memory_date, thumbnail_data, thought_her, thought_him, story_title, story_text, created_at "
            "FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
    return {
        "id": row["id"],
        "memory_date": row["memory_date"],
        "photo": row["thumbnail_data"] or f"/api/memories/{row['id']}/photo",
        "photo_original": f"/api/memories/{row['id']}/photo",
        "thought_her": row["thought_her"],
        "thought_him": row["thought_him"],
        "story_title": row["story_title"],
        "story_text": row["story_text"],
        "created_at": row["created_at"],
    }


def get_memory_photo(memory_id):
    if using_supabase():
        rows = supabase_request(
            "GET",
            f"memories?select=photo_path&id=eq.{memory_id}",
        )
        return supabase_memory_photo_url(rows[0]["photo_path"]) if rows else None
    with DB_LOCK, database() as db:
        row = db.execute(
            "SELECT image_data FROM memories WHERE id = ?", (memory_id,),
        ).fetchone()
    if not row:
        return None
    match = PHOTO_PATTERN.fullmatch(row["image_data"])
    if not match:
        raise ValueError("Не удалось прочитать фотографию.")
    return f"image/{match.group(1)}", base64.b64decode(match.group(2), validate=True)


def delete_memory(memory_id):
    if using_supabase():
        existing = supabase_request(
            "GET",
            f"memories?select=id,photo_path,thumbnail_path&id=eq.{memory_id}",
        )
        if not existing:
            return False
        photo_path = existing[0]["photo_path"]
        story_references = supabase_request(
            "GET",
            "stories?select=id&photo_path=eq."
            f"{urllib.parse.quote(photo_path, safe='')}",
        )
        rows = supabase_request(
            "DELETE",
            f"memories?id=eq.{memory_id}",
            prefer="return=representation",
        )
        if not rows:
            return False
        if story_references:
            return True
        try:
            supabase_storage_request(
                "DELETE",
                "object/memories",
                {"prefixes": [
                    path for path in (photo_path, existing[0].get("thumbnail_path")) if path
                ]},
            )
        except SupabaseError as exc:
            raise SupabaseError(
                f"Воспоминание удалено, но файл не удалось очистить из Storage: {exc}"
            ) from exc
        return True

    with DB_LOCK, database() as db:
        result = db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return result.rowcount > 0


def get_state():
    if using_supabase():
        settings_rows = supabase_request(
            "GET",
            "settings?select=together_since,demob_date&id=eq.1",
        )
        wish_rows = supabase_request(
            "GET",
            "wishes?select=id,title,category,status,created_at,planned_for,planned_time,completed_at,completed_time,photo"
            "&order=status.desc,created_at.desc",
        )
        settings = settings_rows[0] if settings_rows else {}
        return {
            "settings": {
                "together_since": settings.get("together_since") or "",
                "demob_date": settings.get("demob_date") or "",
            },
            "wishes": [
                {
                    **wish,
                    "planned_for": wish.get("planned_for") or "",
                    "planned_time": (wish.get("planned_time") or "")[:5],
                    "completed_at": wish.get("completed_at") or "",
                    "completed_time": (wish.get("completed_time") or "")[:5],
                    "photo": wish.get("photo") or "",
                }
                for wish in wish_rows
            ],
        }
    with DB_LOCK, database() as db:
        settings = db.execute(
            "SELECT together_since, demob_date FROM settings WHERE id = 1"
        ).fetchone()
        wishes = db.execute(
            "SELECT id, title, category, status, created_at, planned_for, planned_time, completed_at, completed_time, photo "
            "FROM wishes ORDER BY CASE status WHEN 'planned' THEN 0 ELSE 1 END, created_at DESC"
        ).fetchall()
    return {
        "settings": dict(settings),
        "wishes": [dict(wish) for wish in wishes],
    }


def telegram_api(token, method, payload):
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            response_body = exc.read(4096).decode("utf-8", "replace")
            response_json = json.loads(response_body)
            if isinstance(response_json, dict):
                detail = response_json.get("description", "")
        except (OSError, json.JSONDecodeError):
            pass
        if token:
            detail = detail.replace(token, "[скрыто]")
        suffix = f": {detail}" if detail else ""
        raise TelegramError(f"Telegram API вернул HTTP {exc.code}{suffix}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TelegramError("Не удалось подключиться к Telegram API.") from exc
    except json.JSONDecodeError as exc:
        raise TelegramError("Telegram API вернул некорректный JSON.") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise TelegramError("Telegram API отклонил запрос.")
    return result["result"]


def notify_wish_completed(wish):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_ids = {
        item.strip()
        for item in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
        if item.strip().isdigit()
    }
    if not token or not allowed_ids:
        return "not_configured"

    delivered = 0
    failed = 0
    for user_id in sorted(allowed_ids):
        try:
            telegram_api(token, "sendMessage", {
                "chat_id": int(user_id),
                "text": f"✅ Приключение сбылось!\n«{wish['title']}»",
            })
            delivered += 1
        except TelegramError as exc:
            failed += 1
            print(f"Не удалось отправить уведомление о приключении пользователю {user_id}: {exc}")
    if failed and delivered:
        return "partial"
    if failed:
        return "failed"
    return "sent"


def create_wish(title, category="Разное"):
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 120:
        raise ValueError("Идея должна содержать от 1 до 120 символов.")
    if not isinstance(category, str) or len(category.strip()) > 30:
        raise ValueError("Название категории должно быть короче 30 символов.")
    wish = {
        "id": uuid.uuid4().hex,
        "title": title.strip(),
        "category": category.strip() or "Разное",
        "status": "planned",
        "created_at": datetime.date.today().isoformat(),
        "planned_for": "",
        "planned_time": "",
        "completed_at": "",
        "completed_time": "",
        "photo": "",
    }
    if using_supabase():
        rows = supabase_request(
            "POST",
            "wishes",
            {
                "id": wish["id"],
                "title": wish["title"],
                "category": wish["category"],
                "status": wish["status"],
                "created_at": wish["created_at"],
            },
            prefer="return=representation",
        )
        if not rows:
            raise SupabaseError("Supabase не подтвердил сохранение идеи.")
        return rows[0]
    with DB_LOCK, database() as db:
        db.execute(
            "INSERT INTO wishes (id, title, category, created_at) VALUES (?, ?, ?, ?)",
            (wish["id"], wish["title"], wish["category"], wish["created_at"]),
        )
    return wish


def parse_reminder(value, now=None):
    if not isinstance(value, str):
        raise ValueError("Напишите время и текст напоминания.")
    value = value.strip()
    value = re.sub(r"^напомни(?:\s+мне)?[,:]?\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"^(сегодня|завтра|послезавтра)\s+в\s+(\d{1,2}:\d{2})",
        r"\1 \2",
        value,
        flags=re.IGNORECASE,
    )
    now = now or datetime.datetime.now().astimezone()
    relative = re.fullmatch(
        r"через\s+(\d{1,3})\s+(минут(?:у|ы)?|мин|час(?:а|ов)?|день|дня|дней)\s+(.+)",
        value,
        re.IGNORECASE,
    )
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        if amount < 1:
            raise ValueError("Укажите положительный срок напоминания.")
        if unit.startswith("мин"):
            delta = datetime.timedelta(minutes=amount)
        elif unit.startswith("час"):
            delta = datetime.timedelta(hours=amount)
        else:
            delta = datetime.timedelta(days=amount)
        remind_at = now + delta
        title = relative.group(3).strip()
    else:
        patterns = (
            (r"^(\d{4}-\d{2}-\d{2})\s+(?:в\s+)?(\d{1,2}:\d{2})\s+(.+)$", "iso"),
            (r"^(сегодня|завтра|послезавтра)\s+(?:в\s+)?(\d{1,2}:\d{2})\s+(.+)$", "relative_date"),
            (r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+(?:в\s+)?(\d{1,2}:\d{2})\s+(.+)$", "date"),
        )
        for pattern, kind in patterns:
            match = re.fullmatch(pattern, value, re.IGNORECASE)
            if not match:
                continue
            try:
                if kind == "iso":
                    date_part, time_part, title = match.groups()
                    date_value = datetime.date.fromisoformat(date_part)
                elif kind == "relative_date":
                    day, time_part, title = match.groups()
                    day_offset = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[day.lower()]
                    date_value = now.date() + datetime.timedelta(days=day_offset)
                else:
                    day, month, year, time_part, title = match.groups()
                    date_value = datetime.date(int(year or now.year), int(month), int(day))
                hour, minute = map(int, time_part.split(":"))
                remind_at = datetime.datetime.combine(
                    date_value, datetime.time(hour=hour, minute=minute), tzinfo=now.tzinfo
                )
            except ValueError as exc:
                raise ValueError("Не понял дату или время. Пример: завтра 18:30 купить цветы.") from exc
            break
        else:
            raise ValueError(
                "Формат: «завтра 18:30 купить цветы» или «через 2 часа позвонить маме»."
            )

    if not title or len(title) > 240:
        raise ValueError("Текст напоминания должен содержать от 1 до 240 символов.")
    if remind_at <= now:
        raise ValueError("Укажите время в будущем.")
    return title, remind_at.astimezone(datetime.timezone.utc).isoformat()


def transcribe_telegram_voice(token, voice):
    duration = voice.get("duration")
    file_size = voice.get("file_size")
    if isinstance(duration, (int, float)) and duration > MAX_TELEGRAM_VOICE_SECONDS:
        raise TelegramError("Голосовое длиннее 3 минут. Отправьте короткую запись.")
    if isinstance(file_size, int) and file_size > MAX_TELEGRAM_VOICE_BYTES:
        raise TelegramError("Голосовое слишком большое для обработки.")

    try:
        file_info = telegram_api(token, "getFile", {"file_id": voice["file_id"]})
    except (KeyError, TelegramError) as exc:
        raise TelegramError("Не удалось получить голосовое из Telegram.") from exc
    file_path = file_info.get("file_path") if isinstance(file_info, dict) else None
    if not isinstance(file_path, str) or not file_path:
        raise TelegramError("Telegram не вернул путь к голосовому файлу.")

    file_url = (
        "https://api.telegram.org/file/bot"
        f"{token}/{urllib.parse.quote(file_path, safe='/')}"
    )
    try:
        with urllib.request.urlopen(file_url, timeout=60) as response:
            audio_bytes = response.read(MAX_TELEGRAM_VOICE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TelegramError("Не удалось скачать голосовое из Telegram.") from exc
    if len(audio_bytes) > MAX_TELEGRAM_VOICE_BYTES:
        raise TelegramError("Голосовое слишком большое для обработки.")

    if len(audio_bytes) <= MAX_EDGE_VOICE_BYTES and using_supabase():
        try:
            return transcribe_voice_with_edge(audio_bytes)
        except TelegramError as exc:
            print(f"Edge Function не распознала голосовое, использую локальную модель: {exc}")

    return transcribe_voice_locally(audio_bytes, file_path)


def transcribe_voice_with_edge(audio_bytes):
    config = supabase_config()
    if config is None:
        raise TelegramError("Supabase не настроен для обработки голосовых.")
    project_url, service_key = config
    voice_secret = hmac.new(
        service_key.encode("utf-8"),
        VOICE_FUNCTION_SECRET_CONTEXT,
        hashlib.sha256,
    ).hexdigest()
    local_now = datetime.datetime.now().astimezone()
    request = urllib.request.Request(
        f"{project_url}/functions/v1/voice-reminder",
        data=audio_bytes,
        headers={
            "apikey": service_key,
            "Content-Type": "audio/ogg",
            "X-Voice-Function-Secret": voice_secret,
            "X-Local-Time": local_now.isoformat(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read(64 * 1024))
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", "replace")
        for secret in (service_key, voice_secret):
            if secret:
                detail = detail.replace(secret, "[скрыто]")
        raise TelegramError(f"Edge Function вернула HTTP {exc.code}: {detail[:300]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TelegramError("Не удалось подключиться к Edge Function.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TelegramError("Edge Function вернула некорректный ответ.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("reminder"), str):
        raise TelegramError("Edge Function вернула напоминание в неожиданном формате.")
    reminder_text = payload["reminder"].strip()
    if not reminder_text:
        raise TelegramError("В голосовом не удалось разобрать время и текст напоминания.")
    try:
        parse_reminder(reminder_text, now=local_now)
    except ValueError as exc:
        raise TelegramError("Распознанное напоминание не содержит корректного будущего времени.") from exc
    return reminder_text


def transcribe_voice_locally(audio_bytes, file_path):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TelegramError(
            "Не установлен модуль распознавания. Установите зависимости командой "
            "python -m pip install -r requirements.txt."
        ) from exc
    try:
        import av
        from huggingface_hub.errors import HfHubHTTPError
    except ImportError as exc:
        raise TelegramError(
            "Не установлены компоненты распознавания. Запустите python -m pip install -r requirements.txt."
        ) from exc

    global VOICE_MODEL
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_file = Path(temp_dir) / (Path(file_path).suffix or ".ogg")
            audio_file.write_bytes(audio_bytes)
            with VOICE_MODEL_LOCK:
                if VOICE_MODEL is None:
                    VOICE_MODEL = WhisperModel("base", device="cpu", compute_type="int8")
                segments, _info = VOICE_MODEL.transcribe(
                    str(audio_file),
                    language="ru",
                    vad_filter=True,
                )
                transcript = " ".join(segment.text.strip() for segment in segments).strip()
    except TypeError as exc:
        if "metadata_errors" in str(exc):
            raise TelegramError(
                "Версия PyAV несовместима с распознаванием. Выполните "
                "python -m pip install -r requirements.txt и перезапустите сайт."
            ) from exc
        raise TelegramError("Ошибка формата голосового файла при распознавании.") from exc
    except (ImportError, OSError, RuntimeError, ValueError, HfHubHTTPError, av.error.FFmpegError) as exc:
        raise TelegramError(
            "Не удалось распознать речь. Проверьте установку модуля и доступ к модели."
        ) from exc
    if not transcript:
        raise TelegramError("Не расслышала текст. Попробуйте записать сообщение ещё раз.")
    return transcript


def create_reminder(value, created_by):
    title, remind_at = parse_reminder(value)
    reminder = {
        "id": uuid.uuid4().hex,
        "title": title,
        "remind_at": remind_at,
        "created_by": str(created_by),
        "sent": False,
        "sent_to": "",
    }
    if using_supabase():
        rows = supabase_request(
            "POST",
            "reminders",
            reminder,
            prefer="return=representation",
        )
        if not rows:
            raise SupabaseError("Supabase не подтвердил сохранение напоминания.")
        return rows[0]
    with DB_LOCK, database() as db:
        db.execute(
            "INSERT INTO reminders (id, title, remind_at, created_by) VALUES (?, ?, ?, ?)",
            (reminder["id"], reminder["title"], reminder["remind_at"], reminder["created_by"]),
        )
    return reminder


def get_due_reminders(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    now = now.astimezone(datetime.timezone.utc).isoformat()
    if using_supabase():
        resource = (
            "reminders?select=id,title,remind_at,created_by,sent,sent_to"
            f"&remind_at=lte.{urllib.parse.quote(now, safe='')}&sent=eq.false&order=remind_at.asc"
        )
        return supabase_request("GET", resource)
    with DB_LOCK, database() as db:
        rows = db.execute(
            "SELECT id, title, remind_at, created_by, sent, sent_to "
            "FROM reminders WHERE sent = 0 AND remind_at <= ? ORDER BY remind_at",
            (now,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_reminder_delivery(reminder_id, sent_to, sent):
    if using_supabase():
        rows = supabase_request(
            "PATCH",
            f"reminders?id=eq.{reminder_id}",
            {"sent_to": sent_to, "sent": sent},
            prefer="return=representation",
        )
        if not rows:
            raise SupabaseError("Не удалось обновить состояние напоминания.")
        return
    with DB_LOCK, database() as db:
        db.execute(
            "UPDATE reminders SET sent_to = ?, sent = ? WHERE id = ?",
            (sent_to, int(sent), reminder_id),
        )


def deliver_due_reminders(token, allowed_ids):
    for reminder in get_due_reminders():
        sent_to = {
            item for item in (reminder.get("sent_to") or "").split(",") if item
        }
        for user_id in sorted(allowed_ids - sent_to):
            try:
                telegram_api(token, "sendMessage", {
                    "chat_id": int(user_id),
                    "text": f"🔔 Напоминание\n{reminder['title']}",
                })
            except TelegramError as exc:
                if "chat not found" in str(exc).lower():
                    warning_key = (reminder["id"], user_id)
                    if warning_key not in TELEGRAM_CHAT_WARNINGS:
                        print(
                            f"Напоминание {reminder['id']}: пользователь {user_id} ещё не запустил бота. "
                            "Попросите его открыть бота и нажать Start."
                        )
                        TELEGRAM_CHAT_WARNINGS.add(warning_key)
                else:
                    print(f"Не удалось отправить напоминание пользователю {user_id}: {exc}")
                continue
            TELEGRAM_CHAT_WARNINGS.discard((reminder["id"], user_id))
            sent_to.add(user_id)
            update_reminder_delivery(
                reminder["id"],
                ",".join(sorted(sent_to)),
                sent_to >= allowed_ids,
            )
        if sent_to >= allowed_ids and not reminder.get("sent"):
            update_reminder_delivery(reminder["id"], ",".join(sorted(sent_to)), True)


def telegram_reply(message, allowed_ids, token=""):
    chat = message.get("chat", {})
    sender_id = str(message.get("from", {}).get("id", ""))
    text = message.get("text", "")
    if chat.get("type") != "private" or sender_id not in allowed_ids:
        return None
    if message.get("voice"):
        try:
            transcript = transcribe_telegram_voice(token, message["voice"])
            reminder = create_reminder(transcript, sender_id)
        except ValueError as exc:
            return str(exc)
        except TelegramError as exc:
            return str(exc)
        except (sqlite3.Error, SupabaseError) as exc:
            print(f"Не удалось сохранить голосовое напоминание: {exc}")
            return "Речь распознана, но не удалось сохранить напоминание в базе."
        local_time = datetime.datetime.fromisoformat(reminder["remind_at"]).astimezone()
        return f"Голосовое распознано. Напомню {local_time:%d.%m в %H:%M}: {reminder['title']}"
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    if text in ("/start", "/help", "ℹ️ Помощь"):
        TELEGRAM_PENDING_ACTIONS.pop(sender_id, None)
        return (
            "Привет! Выберите действие в меню ниже.\n"
            "Напоминание: нажмите кнопку и отправьте «завтра 18:30 купить цветы»."
        )
    if text in ("/add", "➕ Добавить желание"):
        TELEGRAM_PENDING_ACTIONS[sender_id] = "wish"
        return "Напишите желание одним сообщением."
    if text.startswith("/add "):
        TELEGRAM_PENDING_ACTIONS.pop(sender_id, None)
        try:
            wish = create_wish(text[5:].strip())
            return f"Добавила в ваши планы: «{wish['title']}» 💛"
        except ValueError as exc:
            return str(exc)
    if text in ("/list", "📋 Мои желания"):
        TELEGRAM_PENDING_ACTIONS.pop(sender_id, None)
        pending = [
            wish["title"] for wish in get_state()["wishes"]
            if wish["status"] != "done"
        ]
        return "Ваши идеи:\n" + (
            "\n".join(f"• {title}" for title in pending[:20])
            if pending else "Пока нет планов. Добавьте идею командой /add"
        )
    if text in ("/remind", "🔔 Напоминание"):
        TELEGRAM_PENDING_ACTIONS[sender_id] = "reminder"
        return "Когда и о чём напомнить? Например: завтра 18:30 купить цветы."
    if text.startswith("/remind "):
        TELEGRAM_PENDING_ACTIONS.pop(sender_id, None)
        try:
            reminder = create_reminder(text[8:].strip(), sender_id)
        except ValueError as exc:
            return str(exc)
        local_time = datetime.datetime.fromisoformat(reminder["remind_at"]).astimezone()
        return f"Напоминание сохранено на {local_time:%d.%m в %H:%M}: {reminder['title']}"
    pending_action = TELEGRAM_PENDING_ACTIONS.get(sender_id)
    if pending_action == "wish":
        TELEGRAM_PENDING_ACTIONS.pop(sender_id, None)
        try:
            wish = create_wish(text)
            return f"Добавила в ваши планы: «{wish['title']}» 💛"
        except ValueError as exc:
            return str(exc)
    if pending_action == "reminder":
        try:
            reminder = create_reminder(text, sender_id)
        except ValueError as exc:
            return str(exc)
        TELEGRAM_PENDING_ACTIONS.pop(sender_id, None)
        local_time = datetime.datetime.fromisoformat(reminder["remind_at"]).astimezone()
        return f"Напоминание сохранено на {local_time:%d.%m в %H:%M}: {reminder['title']}"
    return "Привет! Добавить идею: /add устроить поездку\nПосмотреть планы: /list"


def telegram_safe_reply(message, allowed_ids, token=""):
    try:
        return telegram_reply(message, allowed_ids, token)
    except TelegramError as exc:
        return str(exc)
    except (sqlite3.Error, SupabaseError) as exc:
        print(f"Не удалось обработать сообщение Telegram: {exc}")
        return "Не удалось обратиться к базе данных. Попробуйте ещё раз позже."


def telegram_poll(token, allowed_ids):
    offset = 0
    TELEGRAM_STATUS["running"] = True
    while True:
        try:
            updates = telegram_api(token, "getUpdates", {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message"],
            })
            TELEGRAM_STATUS["error"] = ""
            for update in updates:
                message = update.get("message", {})
                chat = message.get("chat", {})
                reply = telegram_safe_reply(message, allowed_ids, token)
                offset = update.get("update_id", offset) + 1
                if reply:
                    telegram_api(token, "sendMessage", {
                        "chat_id": chat["id"],
                        "text": reply,
                        "reply_markup": TELEGRAM_KEYBOARD,
                    })
            deliver_due_reminders(token, allowed_ids)
        except (
            OSError,
            TelegramError,
            ValueError,
            sqlite3.Error,
            SupabaseError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as exc:
            TELEGRAM_STATUS["error"] = str(exc)
            print(f"Не удалось связаться с Telegram: {exc}")
            time.sleep(5)


def start_telegram_bot():
    TELEGRAM_STATUS.update(
        enabled=False,
        running=False,
        cloudManaged=False,
        error="",
    )
    if os.environ.get("TELEGRAM_MODE", "").strip().lower() == "cloud":
        TELEGRAM_STATUS.update(enabled=True, cloudManaged=True)
        print("Telegram-бот работает в Supabase; локальный polling отключён.")
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    allowed_ids = {
        item.strip()
        for item in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
        if item.strip().isdigit()
    }
    if not allowed_ids:
        TELEGRAM_STATUS["error"] = "Не заданы TELEGRAM_ALLOWED_USER_IDS."
        print(f"Telegram-бот выключен: {TELEGRAM_STATUS['error']}")
        return
    TELEGRAM_STATUS["enabled"] = True
    threading.Thread(target=telegram_poll, args=(token, allowed_ids), daemon=True).start()
    print("Telegram-бот запущен для настроенных ID.")


class Handler(BaseHTTPRequestHandler):
    server_version = "WishList/1.0"

    def send_bytes(self, status, content_type, body, cache_control="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        self.send_header("Cache-Control", cache_control)
        if os.environ.get("RENDER") or self.headers.get("X-Forwarded-Proto", "").lower() == "https":
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        image_sources = "'self' data:"
        project_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        if project_url:
            parsed_project_url = urllib.parse.urlparse(project_url)
            if parsed_project_url.scheme == "https" and parsed_project_url.netloc:
                image_sources += f" https://{parsed_project_url.netloc}"
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            f"img-src {image_sources}; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, "application/json; charset=utf-8", body)

    def read_json(self, max_bytes=MAX_BODY_BYTES):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Некорректный размер запроса.") from exc
        if length <= 0 or length > max_bytes:
            raise ValueError("Запрос пустой или слишком большой.")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Ожидался JSON-объект.")
        return payload

    def authenticated(self):
        password, signing_secret = auth_config()
        if not password:
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except (CookieError, ValueError):
            return False
        morsel = cookie.get(AUTH_COOKIE_NAME)
        return bool(
            morsel and validate_auth_cookie(morsel.value, signing_secret)
        )

    def require_auth(self):
        password, _signing_secret = auth_config()
        if not password or self.authenticated():
            return True
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            self.send_json(401, {"error": "Сессия истекла. Войдите снова."})
        else:
            self.send_response(303)
            self.send_header("Location", "/login")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
        return False

    def handle_login(self):
        password, signing_secret = auth_config()
        if not password:
            self.send_json(404, {"error": "Вход не настроен."})
            return
        client_id = self.client_address[0]
        if not allow_login_attempt(client_id):
            self.send_json(429, {"error": "Слишком много попыток. Попробуйте через 15 минут."})
            return
        try:
            payload = self.read_json()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.send_json(400, {"error": str(exc) or "Некорректный запрос."})
            return
        supplied_password = payload.get("password")
        if not isinstance(supplied_password, str) or len(supplied_password) > 512:
            supplied_password = ""
        if not hmac.compare_digest(
            supplied_password.encode("utf-8"),
            password.encode("utf-8"),
        ):
            self.send_json(401, {"error": "Неверный пароль."})
            return

        cookie_value, _expiry = create_auth_cookie(signing_secret)
        secure = self.uses_secure_cookies()
        cookie_attributes = [
            f"{AUTH_COOKIE_NAME}={cookie_value}",
            "Path=/",
            f"Max-Age={AUTH_SESSION_SECONDS}",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if secure:
            cookie_attributes.append("Secure")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", "11")
        self.send_header("Set-Cookie", "; ".join(cookie_attributes))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def handle_logout(self):
        secure = "; Secure" if self.uses_secure_cookies() else ""
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", "11")
        self.send_header(
            "Set-Cookie",
            f"{AUTH_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict{secure}",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def uses_secure_cookies(self):
        return bool(os.environ.get("RENDER")) or (
            self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        )

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/login":
            if self.authenticated():
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                body = (STATIC / "login.html").read_bytes()
                self.send_bytes(200, "text/html; charset=utf-8", body)
            except OSError as exc:
                print(f"Не удалось прочитать страницу входа: {exc}")
                self.send_json(500, {"error": "Не удалось загрузить страницу входа."})
            return
        if path in ("/login.js", "/login.css"):
            asset = path[1:]
            content_type = "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"
            try:
                body = (STATIC / asset).read_bytes()
                self.send_bytes(200, content_type, body)
            except OSError as exc:
                print(f"Не удалось прочитать ресурс входа: {exc}")
                self.send_json(500, {"error": "Не удалось загрузить ресурс входа."})
            return
        if path == "/api/state":
            if not self.require_auth():
                return
            try:
                self.send_json(200, get_state())
            except sqlite3.Error as exc:
                print(f"Не удалось прочитать список идей: {exc}")
                self.send_json(500, {"error": "Не удалось загрузить список идей."})
            except SupabaseError as exc:
                print(f"Не удалось прочитать список идей: {exc}")
                self.send_json(502, {"error": str(exc)})
            return
        if path == "/api/memories":
            if not self.require_auth():
                return
            try:
                self.send_json(200, {"memories": get_memories()})
            except (sqlite3.Error, SupabaseError) as exc:
                print(f"Не удалось загрузить воспоминания: {exc}")
                self.send_json(502, {"error": str(exc)})
            return
        photo_match = re.fullmatch(r"/api/memories/([0-9a-f]{32})/photo", path)
        if photo_match:
            if not self.require_auth():
                return
            try:
                photo = get_memory_photo(photo_match.group(1))
                if photo is None:
                    self.send_json(404, {"error": "Воспоминание не найдено."})
                elif isinstance(photo, str):
                    self.send_response(302)
                    self.send_header("Location", photo)
                    self.send_header("Cache-Control", "private, max-age=300")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self.send_bytes(
                        200, photo[0], photo[1], cache_control="private, max-age=3600"
                    )
            except (ValueError, sqlite3.Error, SupabaseError) as exc:
                print(f"Не удалось загрузить фотографию: {exc}")
                self.send_json(502, {"error": str(exc)})
            return
        if path == "/api/stories":
            if not self.require_auth():
                return
            try:
                self.send_json(200, {"stories": get_stories()})
            except (sqlite3.Error, SupabaseError) as exc:
                print(f"Не удалось загрузить истории: {exc}")
                self.send_json(502, {"error": str(exc)})
            return
        if path == "/health":
            self.send_json(200, {
                "ok": True,
                "storage": "supabase" if using_supabase() else "sqlite",
                "telegram": dict(TELEGRAM_STATUS),
                "authentication": bool(auth_config()[0]),
            })
            return
        if not self.require_auth():
            return
        static_files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/memories.html": ("memories.html", "text/html; charset=utf-8"),
            "/stories.html": ("stories.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/memories-app.js": ("memories-app.js", "text/javascript; charset=utf-8"),
            "/stories-app.js": ("stories-app.js", "text/javascript; charset=utf-8"),
            "/stories-page.js": ("stories-page.js", "text/javascript; charset=utf-8"),
            "/pages-stories-app.js": ("pages-stories-app.js", "text/javascript; charset=utf-8"),
            "/memory-gallery.js": ("memory-gallery.js", "text/javascript; charset=utf-8"),
            "/photo-utils.js": ("photo-utils.js", "text/javascript; charset=utf-8"),
            "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/photo1.jpg": ("photo1.jpg", "image/jpeg"),
        }
        asset = static_files.get(path)
        if not asset:
            self.send_json(404, {"error": "Страница не найдена."})
            return
        try:
            content = (STATIC / asset[0]).read_bytes()
        except OSError as exc:
            if path == "/photo1.jpg":
                self.send_json(404, {"error": "Фото не загружено на этот хостинг."})
                return
            print(f"Не удалось прочитать файл интерфейса: {exc}")
            self.send_json(500, {"error": "Не удалось загрузить интерфейс."})
            return
        self.send_bytes(200, asset[1], content)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/auth/login":
            self.handle_login()
            return
        if path == "/auth/logout":
            if not self.require_auth():
                return
            self.handle_logout()
            return
        if not self.require_auth():
            return
        if path not in ("/api/wishes", "/api/memories", "/api/stories"):
            self.send_json(404, {"error": "Страница не найдена."})
            return
        try:
            payload = self.read_json(
                MAX_MEMORY_BODY_BYTES if path == "/api/memories" else MAX_BODY_BYTES
            )
            if path == "/api/memories":
                memory = create_memory(
                    payload.get("memory_date"),
                    payload.get("photo"),
                    payload.get("story_title", ""),
                    payload.get("story_text", ""),
                    payload.get("thumbnail", ""),
                )
                self.send_json(201, {"memory": memory})
                return
            if path == "/api/stories":
                story = create_story(
                    payload.get("date"),
                    payload.get("title"),
                    payload.get("story_text"),
                    payload.get("photo", ""),
                )
                self.send_json(201, {"story": story})
                return
            wish = create_wish(payload.get("title"), payload.get("category", "Разное"))
            self.send_json(201, {"wish": wish})
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.send_json(400, {"error": str(exc) or "Некорректный JSON."})
        except (sqlite3.Error, SupabaseError) as exc:
            print(f"Не удалось сохранить запись: {exc}")
            self.send_json(502, {"error": str(exc)})

    def do_PUT(self):
        if not self.require_auth():
            return
        path = urllib.parse.urlparse(self.path).path
        memory_match = re.fullmatch(r"/api/memories/([0-9a-f]{32})", path)
        if memory_match:
            try:
                payload = self.read_json()
                memory = update_memory(memory_match.group(1), payload)
                if memory is None:
                    self.send_json(404, {"error": "Воспоминание не найдено."})
                    return
                self.send_json(200, {"memory": memory})
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.send_json(400, {"error": str(exc) or "Некорректный запрос."})
            except (sqlite3.Error, SupabaseError) as exc:
                print(f"Не удалось обновить воспоминание: {exc}")
                self.send_json(502, {"error": str(exc)})
            return
        if path == "/api/settings":
            try:
                payload = self.read_json()
                together_since = parse_date(payload.get("together_since", ""), "Дата начала отношений")
                demob_date = parse_date(payload.get("demob_date", ""), "Дата дембеля")
                if using_supabase():
                    rows = supabase_request(
                        "PATCH",
                        "settings?id=eq.1",
                        {
                            "together_since": together_since or None,
                            "demob_date": demob_date or None,
                        },
                        prefer="return=representation",
                    )
                    if not rows:
                        raise SupabaseError("Строка настроек не найдена в Supabase.")
                else:
                    with DB_LOCK, database() as db:
                        db.execute(
                            "UPDATE settings SET together_since = ?, demob_date = ? WHERE id = 1",
                            (together_since, demob_date),
                        )
                self.send_json(200, {"settings": {
                    "together_since": together_since,
                    "demob_date": demob_date,
                }})
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.send_json(400, {"error": str(exc) or "Некорректный JSON."})
            except (sqlite3.Error, SupabaseError) as exc:
                print(f"Не удалось сохранить настройки дат: {exc}")
                self.send_json(502, {"error": str(exc)})
            return

        match = re.fullmatch(r"/api/wishes/([0-9a-f]{32})", path)
        if not match:
            self.send_json(404, {"error": "Идея не найдена."})
            return
        try:
            payload = self.read_json()
            changes = {}
            for key, limit in (("title", 120), ("category", 30)):
                if key in payload:
                    value = payload[key]
                    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
                        raise ValueError(f"Проверьте поле «{key}».")
                    changes[key] = value.strip()
            if "planned_for" in payload:
                changes["planned_for"] = parse_date(payload["planned_for"], "Дата планирования")
                if not changes["planned_for"] and "planned_time" not in payload:
                    changes["planned_time"] = ""
            if "planned_time" in payload:
                changes["planned_time"] = parse_time(payload["planned_time"], "Время планирования")
                if changes["planned_time"] and not changes.get("planned_for"):
                    raise ValueError("Чтобы указать время, сначала выберите дату приключения.")
            if "status" in payload:
                if payload["status"] not in ("planned", "done"):
                    raise ValueError("Некорректный статус идеи.")
                changes["status"] = payload["status"]
                completed_at = datetime.datetime.now().astimezone()
                changes["completed_at"] = (
                    completed_at.date().isoformat() if payload["status"] == "done" else ""
                )
                changes["completed_time"] = (
                    completed_at.strftime("%H:%M")
                    if payload["status"] == "done"
                    else ""
                )
            if "photo" in payload:
                changes["photo"] = validate_photo(payload["photo"])
            if not changes:
                raise ValueError("Нет изменений для сохранения.")
            previous_status = None
            if using_supabase():
                if changes.get("status") == "done":
                    previous_rows = supabase_request(
                        "GET",
                        f"wishes?select=status&id=eq.{match.group(1)}",
                    )
                    if not previous_rows:
                        self.send_json(404, {"error": "Идея не найдена."})
                        return
                    previous_status = previous_rows[0].get("status")
                remote_changes = {
                    key: (
                        value or None
                        if key in ("planned_for", "planned_time", "completed_at", "completed_time")
                        else value
                    )
                    for key, value in changes.items()
                }
                rows = supabase_request(
                    "PATCH",
                    f"wishes?id=eq.{match.group(1)}",
                    remote_changes,
                    prefer="return=representation",
                )
                if not rows:
                    self.send_json(404, {"error": "Идея не найдена."})
                    return
                row = rows[0]
                row["planned_time"] = (row.get("planned_time") or "")[:5]
                row["completed_time"] = (row.get("completed_time") or "")[:5]
            else:
                with DB_LOCK, database() as db:
                    if changes.get("status") == "done":
                        previous_row = db.execute(
                            "SELECT status FROM wishes WHERE id = ?",
                            (match.group(1),),
                        ).fetchone()
                        if previous_row is None:
                            self.send_json(404, {"error": "Идея не найдена."})
                            return
                        previous_status = previous_row["status"]
                    columns = ", ".join(f"{key} = ?" for key in changes)
                    result = db.execute(
                        f"UPDATE wishes SET {columns} WHERE id = ?",
                        [*changes.values(), match.group(1)],
                    )
                    if result.rowcount == 0:
                        self.send_json(404, {"error": "Идея не найдена."})
                        return
                    row = db.execute(
                        "SELECT id, title, category, status, created_at, planned_for, planned_time, completed_at, completed_time, photo "
                        "FROM wishes WHERE id = ?",
                        (match.group(1),),
                    ).fetchone()
                    row = dict(row)
            notification = (
                notify_wish_completed(row)
                if changes.get("status") == "done" and previous_status != "done"
                else "not_applicable"
            )
            self.send_json(200, {
                "wish": row,
                "telegram_notification": notification,
            })
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.send_json(400, {"error": str(exc) or "Некорректный JSON."})
        except (sqlite3.Error, SupabaseError) as exc:
            print(f"Не удалось обновить идею: {exc}")
            self.send_json(502, {"error": str(exc)})

    def do_DELETE(self):
        if not self.require_auth():
            return
        path = urllib.parse.urlparse(self.path).path
        memory_match = re.fullmatch(r"/api/memories/([0-9a-f]{32})", path)
        if memory_match:
            try:
                if not delete_memory(memory_match.group(1)):
                    self.send_json(404, {"error": "Воспоминание не найдено."})
                    return
                self.send_json(200, {"ok": True})
            except (sqlite3.Error, SupabaseError) as exc:
                print(f"Не удалось удалить воспоминание: {exc}")
                self.send_json(502, {"error": str(exc)})
            return
        match = re.fullmatch(r"/api/wishes/([0-9a-f]{32})", path)
        if not match:
            self.send_json(404, {"error": "Идея не найдена."})
            return
        try:
            if using_supabase():
                rows = supabase_request(
                    "DELETE",
                    f"wishes?id=eq.{match.group(1)}",
                    prefer="return=representation",
                )
                deleted = bool(rows)
            else:
                with DB_LOCK, database() as db:
                    result = db.execute("DELETE FROM wishes WHERE id = ?", (match.group(1),))
                    deleted = result.rowcount > 0
            if not deleted:
                self.send_json(404, {"error": "Идея не найдена."})
                return
            self.send_json(200, {"ok": True})
        except (sqlite3.Error, SupabaseError) as exc:
            print(f"Не удалось удалить идею: {exc}")
            self.send_json(502, {"error": str(exc)})

    def log_message(self, message_format, *args):
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(message_format, *args)


def main():
    auth_config()
    if os.environ.get("RENDER") and not using_supabase():
        raise RuntimeError(
            "На Render задайте SUPABASE_URL и SUPABASE_SECRET_KEY; "
            "локальная SQLite не является постоянным хранилищем."
        )
    if using_supabase():
        get_state()
        print("Backend: Supabase (секретный ключ используется только на сервере).")
    else:
        initialize_db()
        print("Backend: локальная SQLite. Для Supabase задайте URL и service-role key.")
    start_telegram_bot()
    port = int(os.environ.get("PORT", "8765"))
    host = "0.0.0.0" if os.environ.get("RENDER") else "127.0.0.1"
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Сайт слушает {host}:{port} (Ctrl+C для остановки)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
