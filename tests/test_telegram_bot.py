import os
import io
import json
import datetime
import contextlib
import io
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import urlopen

import app


class TelegramBotTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_file = self.data_dir / "wishlist.sqlite3"
        self.data_dir.mkdir(exist_ok=True)
        self.env = patch.dict(os.environ, {
            "SUPABASE_URL": "",
            "SUPABASE_SECRET_KEY": "",
            "SUPABASE_SERVICE_ROLE_KEY": "",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_ALLOWED_USER_IDS": "",
            "APP_PASSWORD": "",
            "APP_SESSION_SECRET": "",
            "RENDER": "",
        })
        self.env.start()
        self.paths = patch.multiple(app, DATA_DIR=self.data_dir, DB_FILE=self.db_file)
        self.paths.start()
        app.initialize_db()
        app.TELEGRAM_PENDING_ACTIONS.clear()
        app.TELEGRAM_CHAT_WARNINGS.clear()

    def tearDown(self):
        self.paths.stop()
        self.env.stop()
        self.temp_dir.cleanup()

    def test_allowed_private_add_saves_wish_to_sqlite(self):
        reply = app.telegram_reply({
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456},
            "text": "/add устроить поездку",
        }, {"456"})

        self.assertIn("устроить поездку", reply)
        wishes = app.get_state()["wishes"]
        self.assertTrue(any(wish["title"] == "устроить поездку" for wish in wishes))

    def test_group_and_unapproved_user_are_ignored(self):
        group_reply = app.telegram_reply({
            "chat": {"id": -1, "type": "group"},
            "from": {"id": 456},
            "text": "/add групповая идея",
        }, {"456"})
        unapproved_reply = app.telegram_reply({
            "chat": {"id": 789, "type": "private"},
            "from": {"id": 789},
            "text": "/add чужая идея",
        }, {"456"})

        self.assertIsNone(group_reply)
        self.assertIsNone(unapproved_reply)
        self.assertFalse(any(
            wish["title"] in ("групповая идея", "чужая идея")
            for wish in app.get_state()["wishes"]
        ))

    def test_database_failure_returns_error_instead_of_success(self):
        message = {
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456},
            "text": "/add не сохранится",
        }
        with patch("app.create_wish", side_effect=app.SupabaseError("offline")):
            reply = app.telegram_safe_reply(message, {"456"})

        self.assertEqual(
            reply,
            "Не удалось обратиться к базе данных. Попробуйте ещё раз позже.",
        )

    def test_list_reads_saved_wishes(self):
        app.create_wish("Наше желание")

        reply = app.telegram_reply({
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456},
            "text": "/list",
        }, {"456"})

        self.assertIn("Наше желание", reply)

    def test_keyboard_buttons_add_wishes_and_reminders(self):
        add_prompt = app.telegram_reply({
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456},
            "text": "➕ Добавить желание",
        }, {"456"})
        add_result = app.telegram_reply({
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456},
            "text": "Поехать на море",
        }, {"456"})
        reminder_prompt = app.telegram_reply({
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456},
            "text": "🔔 Напоминание",
        }, {"456"})
        reminder_result = app.telegram_reply({
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456},
            "text": "через 2 часа купить билеты",
        }, {"456"})

        self.assertIn("Напишите желание", add_prompt)
        self.assertIn("Поехать на море", add_result)
        self.assertIn("завтра 18:30", reminder_prompt)
        self.assertIn("купить билеты", reminder_result)
        self.assertTrue(any(
            wish["title"] == "Поехать на море" for wish in app.get_state()["wishes"]
        ))

    def test_reminder_parser_supports_tomorrow_and_relative_hours(self):
        now = datetime.datetime(2026, 11, 11, 12, 0, tzinfo=datetime.timezone.utc)

        title, remind_at = app.parse_reminder("завтра 18:30 купить цветы", now)
        parsed_date = datetime.datetime.fromisoformat(remind_at)
        self.assertEqual(title, "купить цветы")
        self.assertEqual(parsed_date, datetime.datetime(2026, 11, 12, 18, 30, tzinfo=datetime.timezone.utc))

        title, remind_at = app.parse_reminder("через 2 часа позвонить маме", now)
        self.assertEqual(title, "позвонить маме")
        self.assertEqual(
            datetime.datetime.fromisoformat(remind_at),
            datetime.datetime(2026, 11, 11, 14, 0, tzinfo=datetime.timezone.utc),
        )

    def test_reminder_parser_understands_spoken_prefix_and_preposition(self):
        now = datetime.datetime(2026, 11, 11, 12, 0, tzinfo=datetime.timezone.utc)

        title, remind_at = app.parse_reminder("напомни мне завтра в 18:30 купить цветы", now)

        self.assertEqual(title, "купить цветы")
        self.assertEqual(
            datetime.datetime.fromisoformat(remind_at),
            datetime.datetime(2026, 11, 12, 18, 30, tzinfo=datetime.timezone.utc),
        )

    def test_planned_time_parser_validates_time_and_normalizes_seconds(self):
        self.assertEqual(app.parse_time("18:30", "Время"), "18:30")
        self.assertEqual(app.parse_time("18:30:00", "Время"), "18:30")
        self.assertEqual(app.parse_time("", "Время"), "")
        with self.assertRaisesRegex(ValueError, "формате"):
            app.parse_time("24:00", "Время")

    def test_existing_sqlite_wishes_are_migrated_with_empty_planned_time(self):
        legacy_dir = Path(self.temp_dir.name) / "legacy"
        legacy_dir.mkdir()
        legacy_db_file = legacy_dir / "wishlist.sqlite3"
        db = sqlite3.connect(legacy_db_file)
        try:
            db.executescript(
                """
                CREATE TABLE settings (
                    id INTEGER PRIMARY KEY,
                    together_since TEXT NOT NULL DEFAULT '',
                    demob_date TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE wishes (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'planned',
                    created_at TEXT NOT NULL,
                    planned_for TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    photo TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE reminders (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0,
                    sent_to TEXT NOT NULL DEFAULT ''
                );
                INSERT INTO settings (id) VALUES (1);
                INSERT INTO wishes (id, title, category, created_at)
                VALUES ('legacy-wish', 'Старая идея', 'Разное', '2026-01-01');
                """
            )
        finally:
            db.close()

        with patch.multiple(app, DATA_DIR=legacy_dir, DB_FILE=legacy_db_file):
            app.initialize_db()
            with app.database() as db:
                wish = db.execute(
                    "SELECT title, planned_time, completed_time "
                    "FROM wishes WHERE id = 'legacy-wish'"
                ).fetchone()

        self.assertEqual(wish["title"], "Старая идея")
        self.assertEqual(wish["planned_time"], "")
        self.assertEqual(wish["completed_time"], "")

    def test_site_schedule_time_and_completion_notify_allowed_telegram_users_once(self):
        wish = app.create_wish("Устроить пикник у озера")
        sent = []

        def fake_telegram_api(_token, method, payload):
            sent.append((method, payload))
            return {}

        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        def update_wish(payload):
            request = app.urllib.request.Request(
                f"{base_url}/api/wishes/{wish['id']}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with urlopen(request) as response:
                return json.loads(response.read())

        try:
            with (
                patch.dict(os.environ, {
                    "TELEGRAM_BOT_TOKEN": "123456:test-token",
                    "TELEGRAM_ALLOWED_USER_IDS": "456,789",
                }),
                patch("app.telegram_api", side_effect=fake_telegram_api),
            ):
                scheduled = update_wish({
                    "planned_for": "2026-12-25",
                    "planned_time": "18:30",
                })
                self.assertEqual(scheduled["wish"]["planned_for"], "2026-12-25")
                self.assertEqual(scheduled["wish"]["planned_time"], "18:30")
                self.assertEqual(scheduled["telegram_notification"], "not_applicable")

                completed = update_wish({"status": "done"})
                repeated = update_wish({"status": "done"})

            self.assertEqual(completed["telegram_notification"], "sent")
            self.assertEqual(repeated["telegram_notification"], "not_applicable")
            self.assertEqual([payload["chat_id"] for _method, payload in sent], [456, 789])
            self.assertTrue(all(
                payload["text"] == "✅ Приключение сбылось!\n«Устроить пикник у озера»"
                for _method, payload in sent
            ))
            with app.database() as db:
                row = db.execute(
                    "SELECT planned_for, planned_time, completed_at, completed_time, status FROM wishes WHERE id = ?",
                    (wish["id"],),
                ).fetchone()
            self.assertEqual(row["planned_for"], "2026-12-25")
            self.assertEqual(row["planned_time"], "18:30")
            self.assertEqual(row["status"], "done")
            self.assertEqual(row["completed_at"], datetime.date.today().isoformat())
            self.assertRegex(row["completed_time"], r"^\d{2}:\d{2}$")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_site_rejects_planned_time_without_date(self):
        wish = app.create_wish("Сходить на концерт")
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = app.urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/wishes/{wish['id']}",
                data=json.dumps({"planned_time": "18:30"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            with self.assertRaises(app.urllib.error.HTTPError) as raised:
                urlopen(request)
            self.assertEqual(raised.exception.code, 400)
            self.assertIn("сначала выберите дату", raised.exception.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_due_reminder_is_delivered_to_each_allowed_user(self):
        reminder_id = "b" * 32
        due_at = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).isoformat()
        with app.database() as db:
            db.execute(
                "INSERT INTO reminders (id, title, remind_at, created_by) VALUES (?, ?, ?, ?)",
                (reminder_id, "Забрать заказ", due_at, "456"),
            )
        sent = []

        def fake_telegram_api(_token, method, payload):
            sent.append((method, payload))
            return {}

        with patch("app.telegram_api", side_effect=fake_telegram_api):
            app.deliver_due_reminders("unused-token", {"456", "789"})

        self.assertEqual(
            [payload["chat_id"] for _method, payload in sent],
            [456, 789],
        )
        with app.database() as db:
            reminder = db.execute(
                "SELECT sent, sent_to FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
        self.assertEqual(reminder["sent"], 1)
        self.assertEqual(set(reminder["sent_to"].split(",")), {"456", "789"})

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test-value",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    })
    @patch("app.supabase_request")
    def test_reminders_use_supabase_when_configured(self, supabase_request):
        supabase_request.side_effect = lambda _method, _resource, payload=None, **_kwargs: [payload]

        reminder = app.create_reminder("через 2 часа купить цветы", "456")

        self.assertEqual(reminder["title"], "купить цветы")
        self.assertEqual(supabase_request.call_args.args[0], "POST")
        self.assertEqual(supabase_request.call_args.args[1], "reminders")
        self.assertEqual(supabase_request.call_args.args[2]["created_by"], "456")

    def test_voice_transcription_creates_a_local_reminder(self):
        message = {
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456},
            "voice": {"file_id": "opaque"},
        }
        with patch("app.transcribe_telegram_voice", return_value="завтра 18:30 купить цветы"):
            reply = app.telegram_reply(message, {"456"}, "unused-token")

        self.assertIn("Голосовое распознано", reply)
        with app.database() as db:
            row = db.execute(
                "SELECT title FROM reminders ORDER BY remind_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row["title"], "купить цветы")

    def test_voice_audio_is_downloaded_and_passed_to_local_model(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"local audio bytes"

        class FakeModel:
            def transcribe(self, audio_path, language, vad_filter):
                self.audio = Path(audio_path).read_bytes()
                self.options = (language, vad_filter)
                return iter([SimpleNamespace(text="завтра 18:30 купить цветы")]), None

        model = FakeModel()
        with (
            patch("app.telegram_api", return_value={"file_path": "voice.oga"}),
            patch("app.urllib.request.urlopen", return_value=FakeResponse()),
            patch.object(app, "VOICE_MODEL", model),
        ):
            transcript = app.transcribe_telegram_voice(
                "unused-token",
                {"file_id": "opaque-id", "duration": 3, "file_size": 100},
            )

        self.assertEqual(transcript, "завтра 18:30 купить цветы")
        self.assertEqual(model.audio, b"local audio bytes")
        self.assertEqual(model.options, ("ru", True))

    def test_voice_transcription_falls_back_to_local_model_when_edge_fails(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"local audio bytes"

        class FakeModel:
            def transcribe(self, audio_path, language, vad_filter):
                self.options = (language, vad_filter)
                return iter([SimpleNamespace(text="через 2 часа купить цветы")]), None

        model = FakeModel()
        with (
            patch("app.telegram_api", return_value={"file_path": "voice.oga"}),
            patch("app.urllib.request.urlopen", return_value=FakeResponse()),
            patch("app.using_supabase", return_value=True),
            patch("app.transcribe_voice_with_edge", side_effect=app.TelegramError("offline")),
            patch.object(app, "VOICE_MODEL", model),
        ):
            transcript = app.transcribe_telegram_voice(
                "unused-token",
                {"file_id": "opaque-id", "duration": 3, "file_size": 100},
            )

        self.assertEqual(transcript, "через 2 часа купить цветы")
        self.assertEqual(model.options, ("ru", True))

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test-value",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    })
    def test_voice_transcription_uses_edge_function_with_derived_secret(self):
        response_body = json.dumps({
            "reminder": "через 2 часа купить цветы",
        }).encode("utf-8")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return response_body

        with patch("app.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            reminder = app.transcribe_voice_with_edge(b"audio bytes")

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://example.supabase.co/functions/v1/voice-reminder",
        )
        self.assertEqual(request.data, b"audio bytes")
        self.assertEqual(request.get_header("Content-type"), "audio/ogg")
        self.assertTrue(request.get_header("X-voice-function-secret"))
        self.assertEqual(reminder, "через 2 часа купить цветы")

    def test_incompatible_pyav_error_has_a_recovery_instruction(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"audio"

        model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        ))
        with (
            patch("app.telegram_api", return_value={"file_path": "voice.oga"}),
            patch("app.urllib.request.urlopen", return_value=FakeResponse()),
            patch.object(app, "VOICE_MODEL", model),
        ):
            with self.assertRaisesRegex(app.TelegramError, "перезапустите сайт"):
                app.transcribe_telegram_voice(
                    "unused-token",
                    {"file_id": "opaque-id", "duration": 3, "file_size": 100},
                )

    def test_chat_not_found_keeps_reminder_pending_and_logs_once(self):
        reminder_id = "c" * 32
        due_at = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).isoformat()
        with app.database() as db:
            db.execute(
                "INSERT INTO reminders (id, title, remind_at, created_by) VALUES (?, ?, ?, ?)",
                (reminder_id, "Купить цветы", due_at, "456"),
            )

        def fake_send(_token, _method, payload):
            if payload["chat_id"] == 789:
                raise app.TelegramError("Telegram API вернул HTTP 400: Bad Request: chat not found")
            return {}

        output = io.StringIO()
        with patch("app.telegram_api", side_effect=fake_send), contextlib.redirect_stdout(output):
            app.deliver_due_reminders("unused-token", {"456", "789"})
            app.deliver_due_reminders("unused-token", {"456", "789"})

        self.assertEqual(output.getvalue().count("нажать Start"), 1)
        with app.database() as db:
            reminder = db.execute(
                "SELECT sent, sent_to FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
        self.assertEqual(reminder["sent"], 0)
        self.assertEqual(reminder["sent_to"], "456")

    def test_site_serves_bot_link_and_photo(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base_url) as response:
                page = response.read().decode("utf-8")
            with urlopen(f"{base_url}/photo1.jpg") as response:
                photo = response.read()
                content_type = response.headers["Content-Type"]

            self.assertIn("https://t.me/loveibeautifulbot", page)
            self.assertEqual(content_type, "image/jpeg")
            self.assertTrue(photo.startswith(b"\xff\xd8\xff"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_auth_cookie_is_signed_and_expires(self):
        secret = "s" * 40
        cookie, _expiry = app.create_auth_cookie(secret, now=1000)
        self.assertTrue(app.validate_auth_cookie(cookie, secret, now=1001))
        self.assertFalse(app.validate_auth_cookie(cookie, "x" * 40, now=1001))
        self.assertFalse(
            app.validate_auth_cookie(
                cookie,
                secret,
                now=1000 + app.AUTH_SESSION_SECONDS,
            )
        )

    def test_auth_configuration_rejects_short_or_partial_secrets(self):
        with patch.dict(os.environ, {
            "APP_PASSWORD": "short",
            "APP_SESSION_SECRET": "k" * 48,
            "RENDER": "",
        }):
            with self.assertRaisesRegex(RuntimeError, "12 символов"):
                app.auth_config()
        with patch.dict(os.environ, {
            "APP_PASSWORD": "",
            "APP_SESSION_SECRET": "k" * 48,
            "RENDER": "",
        }):
            with self.assertRaisesRegex(RuntimeError, "одновременно"):
                app.auth_config()
        with patch.dict(os.environ, {
            "APP_PASSWORD": "",
            "APP_SESSION_SECRET": "",
            "RENDER": "true",
        }):
            with self.assertRaisesRegex(RuntimeError, "Render"):
                app.auth_config()

    def test_hosted_site_requires_password_and_accepts_signed_session(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None

        client = urllib.request.build_opener(NoRedirect)
        credentials = {
            "APP_PASSWORD": "private-couple-password",
            "APP_SESSION_SECRET": "k" * 48,
            "RENDER": "true",
        }
        try:
            with patch.dict(os.environ, credentials):
                try:
                    client.open(base_url, timeout=3)
                    self.fail("Unauthenticated homepage should redirect to login.")
                except urllib.error.HTTPError as response:
                    self.assertEqual(response.code, 303)
                    self.assertEqual(response.headers["Location"], "/login")

                with self.assertRaises(urllib.error.HTTPError) as response:
                    client.open(f"{base_url}/api/state", timeout=3)
                self.assertEqual(response.exception.code, 401)

                with self.assertRaises(urllib.error.HTTPError) as response:
                   client.open(f"{base_url}/api/memories", timeout=3)
                self.assertEqual(response.exception.code, 401)

                protected_writes = [
                    urllib.request.Request(
                        f"{base_url}/api/wishes",
                        data=json.dumps({"title": "test"}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    urllib.request.Request(
                        f"{base_url}/api/memories",
                        data=json.dumps({
                            "memory_date": "2025-01-01",
                            "photo": "data:image/jpeg;base64,dGVzdA==",
                        }).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    urllib.request.Request(
                        f"{base_url}/api/memories/{'a' * 32}",
                        data=json.dumps({"thought_her": "private"}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="PUT",
                    ),
                    urllib.request.Request(
                        f"{base_url}/api/settings",
                        data=json.dumps({}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="PUT",
                    ),
                    urllib.request.Request(
                        f"{base_url}/api/wishes/{'a' * 32}",
                        method="DELETE",
                    ),
                ]
                for protected_write in protected_writes:
                    with self.assertRaises(urllib.error.HTTPError) as response:
                        client.open(protected_write, timeout=3)
                    self.assertEqual(response.exception.code, 401)

                wrong_password_request = urllib.request.Request(
                    f"{base_url}/auth/login",
                    data=json.dumps({"password": "wrong-password"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as response:
                    client.open(wrong_password_request, timeout=3)
                self.assertEqual(response.exception.code, 401)

                request = urllib.request.Request(
                    f"{base_url}/auth/login",
                    data=json.dumps({"password": credentials["APP_PASSWORD"]}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with client.open(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                    self.assertIn("HttpOnly", response.headers["Set-Cookie"])
                    self.assertIn("Secure", response.headers["Set-Cookie"])

                request = urllib.request.Request(
                    f"{base_url}/api/state",
                    headers={"Cookie": cookie},
                )
                with client.open(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("wishes", json.loads(response.read()))

                request = urllib.request.Request(
                    f"{base_url}/memories.html",
                    headers={"Cookie": cookie},
                )
                with client.open(request) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"memories-cover", response.read())

                request = urllib.request.Request(
                    f"{base_url}/memory-gallery.js",
                    headers={"Cookie": cookie},
                )
                with client.open(request) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"initMemoryGallery", response.read())

                logout_request = urllib.request.Request(
                    f"{base_url}/auth/logout",
                    headers={"Cookie": cookie},
                    method="POST",
                )
                with client.open(logout_request, timeout=3) as response:
                    self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_login_rate_limit_blocks_repeated_attempts(self):
        self.assertTrue(app.allow_login_attempt("test-client", now=100))
        for attempt in range(9):
            self.assertTrue(
                app.allow_login_attempt("test-client", now=100 + attempt)
            )
        self.assertFalse(app.allow_login_attempt("test-client", now=109))

    def test_telegram_http_error_does_not_expose_bot_token(self):
        token = "123456:secret-token"
        error = app.urllib.error.HTTPError(
            f"https://api.telegram.org/bot{token}/getUpdates",
            401,
            "Unauthorized",
            {},
            io.BytesIO(json.dumps({
                "ok": False,
                "description": "Conflict: terminated by other getUpdates request",
            }).encode("utf-8")),
        )
        with patch("app.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(app.TelegramError) as raised:
                app.telegram_api(token, "getUpdates", {})

        self.assertNotIn(token, str(raised.exception))
        self.assertIn("Conflict", str(raised.exception))

    def test_start_bot_exposes_missing_allowed_ids(self):
        app.TELEGRAM_STATUS.update(
            enabled=False,
            running=False,
            cloudManaged=False,
            error="",
        )
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123456:valid-token",
            "TELEGRAM_ALLOWED_USER_IDS": "",
            "TELEGRAM_MODE": "",
        }):
            app.start_telegram_bot()

        self.assertFalse(app.TELEGRAM_STATUS["enabled"])
        self.assertIn("TELEGRAM_ALLOWED_USER_IDS", app.TELEGRAM_STATUS["error"])

    def test_cloud_managed_bot_does_not_start_local_polling(self):
        app.TELEGRAM_STATUS.update(
            enabled=False,
            running=False,
            cloudManaged=False,
            error="",
        )
        with (
            patch.dict(os.environ, {"TELEGRAM_MODE": "cloud"}),
            patch("app.threading.Thread") as thread,
        ):
            app.start_telegram_bot()

        thread.assert_not_called()
        self.assertTrue(app.TELEGRAM_STATUS["enabled"])
        self.assertTrue(app.TELEGRAM_STATUS["cloudManaged"])


if __name__ == "__main__":
    unittest.main()
