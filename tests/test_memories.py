import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import app


class MemoriesApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env = patch.dict(os.environ, {
            "SUPABASE_URL": "",
            "SUPABASE_SECRET_KEY": "",
            "SUPABASE_SERVICE_ROLE_KEY": "",
            "APP_PASSWORD": "",
            "APP_SESSION_SECRET": "",
        })
        self.env.start()
        self.paths = patch.multiple(
            app,
            DATA_DIR=self.data_dir,
            DB_FILE=self.data_dir / "wishlist.sqlite3",
        )
        self.paths.start()
        app.initialize_db()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.paths.stop()
        self.env.stop()
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())

    def test_create_sort_and_edit_memories(self):
        photo = "data:image/jpeg;base64," + base64.b64encode(b"photo").decode("ascii")
        _, older = self.request("POST", "/api/memories", {
            "memory_date": "2024-01-02",
            "photo": photo,
        })
        _, newer = self.request("POST", "/api/memories", {
            "memory_date": "2025-03-04",
            "photo": photo,
        })

        _, result = self.request("GET", "/api/memories")
        self.assertEqual(
            [memory["id"] for memory in result["memories"]],
            [newer["memory"]["id"], older["memory"]["id"]],
        )

        _, updated = self.request("PUT", f"/api/memories/{older['memory']['id']}", {
            "memory_date": "2026-05-06",
            "thought_her": "Я была счастлива",
            "thought_him": "Я тоже",
        })
        self.assertEqual(updated["memory"]["thought_her"], "Я была счастлива")
        self.assertEqual(updated["memory"]["thought_him"], "Я тоже")

        _, result = self.request("GET", "/api/memories")
        self.assertEqual(result["memories"][0]["id"], older["memory"]["id"])

    def test_create_requires_date_and_photo(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("POST", "/api/memories", {
                "memory_date": "",
                "photo": "",
            })
        self.assertEqual(context.exception.code, 400)

    def test_thoughts_are_limited_to_2000_characters(self):
        photo = "data:image/jpeg;base64," + base64.b64encode(b"photo").decode("ascii")
        _, result = self.request("POST", "/api/memories", {
            "memory_date": "2024-01-02",
            "photo": photo,
        })
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("PUT", f"/api/memories/{result['memory']['id']}", {
                "thought_her": "x" * 2001,
            })
        self.assertEqual(context.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
