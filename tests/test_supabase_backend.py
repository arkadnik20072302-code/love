import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import app
from scripts import check_supabase, migrate_to_supabase


class SupabaseRequestTests(unittest.TestCase):
    def test_local_migration_copies_reminders_to_supabase(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = os.path.join(temp_dir, "data")
            os.makedirs(data_dir)
            db_file = os.path.join(data_dir, "wishlist.sqlite3")
            calls = []

            def fake_supabase_request(method, resource, payload=None, prefer=None):
                calls.append((method, resource, payload))
                return []

            with (
                patch.dict(os.environ, {
                    "SUPABASE_URL": "https://example.supabase.co",
                    "SUPABASE_SECRET_KEY": "sb_secret_test-value",
                    "SUPABASE_SERVICE_ROLE_KEY": "",
                }),
                patch.multiple(app, DATA_DIR=Path(data_dir), DB_FILE=Path(db_file)),
                patch("app.supabase_request", side_effect=fake_supabase_request),
            ):
                app.initialize_db()
                with app.database() as db:
                    db.execute(
                        "INSERT INTO reminders "
                        "(id, title, remind_at, created_by, sent, sent_to) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            "f" * 32,
                            "Купить цветы",
                            "2026-11-12T12:30:00+00:00",
                            "456",
                            1,
                            "456",
                        ),
                    )

                migrate_to_supabase.migrate()

            reminder_posts = [
                call for call in calls
                if call[0] == "POST" and call[1] == "reminders?on_conflict=id"
            ]
            self.assertEqual(len(reminder_posts), 1)
            self.assertTrue(reminder_posts[0][2]["sent"])
            self.assertEqual(reminder_posts[0][2]["sent_to"], "456")

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test-value",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    })
    def test_api_persists_planned_date_and_time_to_supabase(self):
        wish_id = "d" * 32
        calls = []

        def fake_supabase_request(method, resource, payload=None, prefer=None):
            calls.append((method, resource, payload))
            if method == "PATCH":
                return [{
                    "id": wish_id,
                    "title": "Сходить на концерт",
                    "category": "Разное",
                    "status": "planned",
                    "created_at": "2026-11-11",
                    "planned_for": payload["planned_for"],
                    "planned_time": f"{payload['planned_time']}:00",
                    "completed_at": None,
                    "photo": "",
                }]
            raise AssertionError(f"Unexpected Supabase request: {method} {resource}")

        with patch("app.supabase_request", side_effect=fake_supabase_request):
            server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/wishes/{wish_id}",
                    data=json.dumps({
                        "planned_for": "2026-12-25",
                        "planned_time": "18:30",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urllib.request.urlopen(request) as response:
                    result = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

        self.assertEqual(result["wish"]["planned_time"], "18:30")
        self.assertEqual(calls[0][0:2], ("PATCH", f"wishes?id=eq.{wish_id}"))
        self.assertEqual(calls[0][2]["planned_for"], "2026-12-25")
        self.assertEqual(calls[0][2]["planned_time"], "18:30")

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test-value",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    })
    def test_diagnostic_verifies_wish_insert_read_and_delete(self):
        stored_wishes = []
        calls = []

        def fake_supabase_request(method, resource, payload=None, prefer=None):
            calls.append((method, resource))
            if method == "GET" and resource in ("settings?select=id", "wishes?select=id"):
                return []
            if method == "POST" and resource == "wishes":
                stored_wishes.append(payload)
                return [payload]
            if method == "GET" and resource.startswith("wishes?select=id,title&id=eq."):
                wish_id = resource.split("eq.", 1)[1]
                return [wish for wish in stored_wishes if wish["id"] == wish_id]
            if method == "DELETE" and resource.startswith("wishes?id=eq."):
                wish_id = resource.split("eq.", 1)[1]
                deleted = [wish for wish in stored_wishes if wish["id"] == wish_id]
                stored_wishes[:] = [wish for wish in stored_wishes if wish["id"] != wish_id]
                return deleted
            if method == "GET" and resource.startswith("wishes?select=id&id=eq."):
                wish_id = resource.split("eq.", 1)[1]
                return [wish for wish in stored_wishes if wish["id"] == wish_id]
            raise AssertionError(f"Unexpected Supabase request: {method} {resource}")

        with patch("app.supabase_request", side_effect=fake_supabase_request):
            check_supabase.main()

        self.assertEqual(stored_wishes, [])
        self.assertEqual(
            [method for method, _resource in calls].count("POST"),
            1,
        )
        self.assertEqual(
            [method for method, _resource in calls].count("DELETE"),
            1,
        )

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test-secret",
    })
    @patch("app.urllib.request.urlopen")
    def test_request_keeps_service_key_server_side(self, urlopen):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'[{"id":"1"}]'

        urlopen.return_value = Response()

        result = app.supabase_request(
            "POST",
            "wishes",
            {"title": "test"},
            prefer="return=representation",
        )

        self.assertEqual(result, [{"id": "1"}])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.supabase.co/rest/v1/wishes")
        self.assertEqual(request.get_header("Apikey"), "test-secret")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertNotIn("test-secret", request.data.decode("utf-8"))

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test-value",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    })
    @patch("app.urllib.request.urlopen")
    def test_new_secret_key_is_not_sent_as_bearer(self, urlopen):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'[{"id":1}]'

        urlopen.return_value = Response()

        app.supabase_request("POST", "settings", {"id": 1})

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Apikey"), "sb_secret_test-value")
        self.assertIsNone(request.get_header("Authorization"))

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test-secret",
    })
    @patch("app.urllib.request.urlopen")
    def test_http_error_reports_empty_body_reason_and_request_id(self, urlopen):
        error = app.urllib.error.HTTPError(
            "https://example.supabase.co/rest/v1/settings",
            400,
            "Bad Request",
            {"sb-request-id": "request-123"},
            None,
        )
        urlopen.side_effect = error

        with self.assertRaisesRegex(
            app.SupabaseError,
            r"HTTP 400 для https://example\.supabase\.co/rest/v1/settings, "
            r"request id request-123: ответ API пустой \(Bad Request\)",
        ):
            app.supabase_request("POST", "settings", {"id": 1})

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test-secret",
    })
    def test_delete_removes_row_from_remote_state(self):
        wishes = [{
            "id": "a" * 32,
            "title": "Наша идея",
            "category": "Разное",
            "status": "planned",
            "created_at": "2026-11-11",
            "planned_for": None,
            "completed_at": None,
            "photo": "",
        }]
        calls = []

        def fake_supabase_request(method, resource, payload=None, prefer=None):
            calls.append((method, resource, payload, prefer))
            if resource.startswith("settings?"):
                return [{"together_since": "2025-05-05", "demob_date": "2027-07-06"}]
            if resource.startswith("wishes?") and method == "GET":
                return list(wishes)
            if resource.startswith("wishes?") and method == "DELETE":
                wish_id = resource.split("eq.", 1)[1]
                deleted = [wish for wish in wishes if wish["id"] == wish_id]
                wishes[:] = [wish for wish in wishes if wish["id"] != wish_id]
                return deleted
            raise AssertionError(f"Unexpected Supabase request: {method} {resource}")

        with patch("app.supabase_request", side_effect=fake_supabase_request):
            server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urllib.request.urlopen(f"{base_url}/health") as response:
                    health = json.loads(response.read())
                self.assertEqual(health["storage"], "supabase")
                self.assertIn("telegram", health)
                self.assertFalse(health["telegram"]["enabled"])

                with urllib.request.urlopen(f"{base_url}/api/state") as response:
                    before = json.loads(response.read())
                self.assertEqual(len(before["wishes"]), 1)
                self.assertEqual(before["settings"]["together_since"], "2025-05-05")

                request = urllib.request.Request(
                    f"{base_url}/api/wishes/{'a' * 32}",
                    method="DELETE",
                )
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(json.loads(response.read())["ok"])

                with urllib.request.urlopen(f"{base_url}/api/state") as response:
                    after = json.loads(response.read())
                self.assertEqual(after["wishes"], [])
                self.assertTrue(any(
                    method == "DELETE" and resource == f"wishes?id=eq.{'a' * 32}"
                    for method, resource, _payload, _prefer in calls
                ))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
