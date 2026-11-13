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
            "story_title": "Наша первая прогулка",
            "story_text": "Долго гуляли и смеялись.",
        })
        _, newer = self.request("POST", "/api/memories", {
            "memory_date": "2025-03-04",
            "photo": photo,
        })
        _, same_day = self.request("POST", "/api/memories", {
            "memory_date": "2025-03-04",
            "photo": photo,
            "story_title": "Ещё один кадр",
            "story_text": "Короткая отдельная история.",
        })

        _, result = self.request("GET", "/api/memories")
        self.assertEqual(
            [memory["id"] for memory in result["memories"]],
            [same_day["memory"]["id"], newer["memory"]["id"], older["memory"]["id"]],
        )
        self.assertEqual(older["memory"]["story_title"], "Наша первая прогулка")

        _, updated = self.request("PUT", f"/api/memories/{older['memory']['id']}", {
            "memory_date": "2026-05-06",
            "thought_her": "Я была счастлива",
            "thought_him": "Я тоже",
            "story_title": "Самый тёплый день",
            "story_text": "Провели вместе целый день.",
        })
        self.assertEqual(updated["memory"]["thought_her"], "Я была счастлива")
        self.assertEqual(updated["memory"]["thought_him"], "Я тоже")
        self.assertEqual(updated["memory"]["story_text"], "Провели вместе целый день.")

        _, result = self.request("GET", "/api/memories")
        self.assertEqual(result["memories"][0]["id"], older["memory"]["id"])
        _, stories = self.request("GET", "/api/stories")
        self.assertEqual(
            [story["title"] for story in stories["stories"]],
            ["Самый тёплый день", "Ещё один кадр"],
        )

    def test_large_memory_keeps_a_small_list_preview_and_serves_original_on_demand(self):
        image_bytes = b"x" * (4 * 1024 * 1024)
        photo = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
        thumbnail = "data:image/webp;base64," + base64.b64encode(b"small preview").decode("ascii")
        _, created = self.request("POST", "/api/memories", {
            "memory_date": "2025-03-04",
            "photo": photo,
            "thumbnail": thumbnail,
        })
        memory_id = created["memory"]["id"]

        _, result = self.request("GET", "/api/memories")
        memory = result["memories"][0]
        self.assertEqual(memory["photo"], thumbnail)
        self.assertEqual(memory["photo_original"], f"/api/memories/{memory_id}/photo")
        self.assertLess(len(json.dumps(result)), 1000)

        with urllib.request.urlopen(
            f"{self.base_url}/api/memories/{memory_id}/photo"
        ) as response:
            self.assertEqual(response.headers["Content-Type"], "image/jpeg")
            self.assertEqual(response.read(), image_bytes)

    def test_create_stories_with_optional_photo_and_sort_by_date(self):
        photo = "data:image/jpeg;base64," + base64.b64encode(b"photo").decode("ascii")
        _, older = self.request("POST", "/api/stories", {
            "date": "2024-01-02",
            "title": "Вечерняя прогулка",
            "story_text": "Долго гуляли и разговаривали.",
        })
        _, newer = self.request("POST", "/api/stories", {
            "date": "2025-03-04",
            "title": "Первый снег",
            "story_text": "Вышли гулять сразу после снегопада.",
            "photo": photo,
        })

        self.assertEqual(older["story"]["photo"], "")
        self.assertEqual(newer["story"]["photo"], photo)
        _, result = self.request("GET", "/api/stories")
        self.assertEqual(
            [story["title"] for story in result["stories"]],
            ["Первый снег", "Вечерняя прогулка"],
        )
        self.assertEqual(result["stories"][0]["photo"], photo)

    def test_legacy_photo_stories_migrate_to_independent_stories(self):
        memory_id = "a" * 32
        photo = "data:image/jpeg;base64," + base64.b64encode(b"photo").decode("ascii")
        with app.database() as db:
            db.execute(
                "INSERT INTO memories "
                "(id, memory_date, image_data, thought_her, thought_him, story_title, story_text, created_at) "
                "VALUES (?, ?, ?, '', '', ?, ?, ?)",
                (
                    memory_id,
                    "2024-01-02",
                    photo,
                    "Старый заголовок",
                    "Старый рассказ",
                    "2024-01-02T00:00:00+00:00",
                ),
            )

        app.initialize_db()
        _, result = self.request("GET", "/api/stories")
        self.assertEqual(len(result["stories"]), 1)
        self.assertEqual(result["stories"][0]["title"], "Старый заголовок")

        self.request("DELETE", f"/api/memories/{memory_id}")
        _, result = self.request("GET", "/api/stories")
        self.assertEqual(len(result["stories"]), 1)
        self.assertEqual(result["stories"][0]["story_text"], "Старый рассказ")

    def test_story_requires_title_text_and_valid_date(self):
        payload = {
            "date": "2024-01-02",
            "title": "История",
            "story_text": "Рассказ",
        }
        for changes in (
            {"date": ""},
            {"title": " "},
            {"story_text": ""},
            {"title": "x" * 121},
            {"story_text": "x" * 8001},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(urllib.error.HTTPError) as context:
                    self.request("POST", "/api/stories", {**payload, **changes})
                self.assertEqual(context.exception.code, 400)

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

    def test_story_fields_are_limited_and_memory_can_be_deleted(self):
        photo = "data:image/jpeg;base64," + base64.b64encode(b"photo").decode("ascii")
        _, result = self.request("POST", "/api/memories", {
            "memory_date": "2024-01-02",
            "photo": photo,
            "story_title": "Прогулка",
            "story_text": "История",
        })
        memory_id = result["memory"]["id"]
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("PUT", f"/api/memories/{memory_id}", {
                "story_text": "x" * 8001,
            })
        self.assertEqual(context.exception.code, 400)

        status, deleted = self.request("DELETE", f"/api/memories/{memory_id}")
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])
        _, memories = self.request("GET", "/api/memories")
        self.assertEqual(memories["memories"], [])


if __name__ == "__main__":
    unittest.main()
