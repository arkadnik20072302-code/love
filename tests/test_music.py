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


class MusicApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
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
            DATA_DIR=Path(self.temp_dir.name),
            DB_FILE=Path(self.temp_dir.name) / "wishlist.sqlite3",
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

    def test_create_list_and_delete_yandex_music_tracks(self):
        status, first = self.request("POST", "/api/music", {
            "url": "https://music.yandex.ru/album/12345/track/67890?utm_source=share",
            "title": "Песня нашего лета",
        })
        self.assertEqual(status, 201)
        self.assertEqual(first["track"]["yandex_url"], "https://music.yandex.ru/album/12345/track/67890")
        self.assertEqual(first["track"]["album_id"], "12345")

        _, second = self.request("POST", "/api/music", {
            "url": "https://music.yandex.ru/track/98765",
        })
        _, result = self.request("GET", "/api/music")
        self.assertEqual(
            [track["id"] for track in result["tracks"]],
            [first["track"]["id"], second["track"]["id"]],
        )
        self.assertEqual(second["track"]["title"], "Трек 98765")

        status, deleted = self.request("DELETE", f"/api/music/{first['track']['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])
        _, result = self.request("GET", "/api/music")
        self.assertEqual([track["id"] for track in result["tracks"]], [second["track"]["id"]])

    def test_music_rejects_non_yandex_urls_and_non_track_links(self):
        for url in (
            "http://music.yandex.ru/album/12/track/34",
            "https://example.com/album/12/track/34",
            "https://music.yandex.ru/album/12",
            "https://music.yandex.ru/album/xx/track/34",
        ):
            with self.subTest(url=url):
                with self.assertRaises(urllib.error.HTTPError) as context:
                    self.request("POST", "/api/music", {"url": url, "title": "Test"})
                self.assertEqual(context.exception.code, 400)

    def test_music_rejects_long_titles(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("POST", "/api/music", {
                "url": "https://music.yandex.ru/track/123",
                "title": "x" * 121,
            })
        self.assertEqual(context.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
