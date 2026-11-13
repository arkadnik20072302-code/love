import base64
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_pages import build_pages, is_publishable_key


class PagesBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "static"
        self.output = self.root / "dist"
        self.source.mkdir()
        (self.source / "index.html").write_text(
            '<link rel="stylesheet" href="/style.css">'
            '<script src="/theme.js" defer></script>'
            '<script src="/app.js" defer></script>'
            '<img src="/photo1.jpg">',
            encoding="utf-8",
        )
        (self.source / "login.html").write_text(
            '<link rel="stylesheet" href="/login.css">'
            '<script src="/login.js" defer></script>',
            encoding="utf-8",
        )
        (self.source / "memories.html").write_text(
            '<link rel="stylesheet" href="./style.css">'
            '<script type="module" src="./memories-app.js"></script>',
            encoding="utf-8",
        )
        (self.source / "stories.html").write_text(
            '<link rel="stylesheet" href="./style.css">'
            '<script type="module" src="./stories-app.js"></script>',
            encoding="utf-8",
        )
        (self.source / "app.js").write_text("local backend client", encoding="utf-8")
        (self.source / "login.js").write_text("local login client", encoding="utf-8")
        (self.source / "memories-app.js").write_text("local memories client", encoding="utf-8")
        (self.source / "stories-app.js").write_text("local stories client", encoding="utf-8")
        (self.source / "photo1.jpg").write_bytes(b"homepage photo fixture")
        (self.source / "style.css").write_text("body {}", encoding="utf-8")
        (self.source / "pages-app.js").write_text("pages client", encoding="utf-8")
        (self.source / "pages-login.js").write_text("pages login", encoding="utf-8")
        (self.source / "pages-memories-app.js").write_text("pages memories", encoding="utf-8")
        (self.source / "memory-gallery.js").write_text("shared gallery", encoding="utf-8")
        (self.source / "pages-stories-app.js").write_text("pages stories client", encoding="utf-8")
        (self.source / "theme.js").write_text("theme switch", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_build_uses_relative_pages_assets_and_includes_home_photo(self):
        build_pages(
            self.source,
            self.output,
            "https://example.supabase.co",
            "sb_publishable_example",
        )

        index = (self.output / "index.html").read_text(encoding="utf-8")
        login = (self.output / "login.html").read_text(encoding="utf-8")
        memories = (self.output / "memories.html").read_text(encoding="utf-8")
        stories = (self.output / "stories.html").read_text(encoding="utf-8")
        self.assertIn('href="./style.css"', index)
        self.assertIn('src="./theme.js"', index)
        self.assertIn('src="./pages-app.js"', index)
        self.assertIn('src="./pages-login.js"', login)
        self.assertIn('src="./pages-memories-app.js"', memories)
        self.assertIn('src="./pages-stories-app.js"', stories)
        self.assertTrue((self.output / "memory-gallery.js").exists())
        self.assertTrue((self.output / "theme.js").exists())
        self.assertFalse((self.output / "app.js").exists())
        self.assertFalse((self.output / "login.js").exists())
        self.assertFalse((self.output / "memories-app.js").exists())
        self.assertFalse((self.output / "stories-app.js").exists())
        self.assertEqual(
            (self.output / "photo1.jpg").read_bytes(),
            b"homepage photo fixture",
        )
        self.assertIn('"publishableKey":"sb_publishable_example"', (self.output / "pages-config.js").read_text(encoding="utf-8"))

    def test_build_rejects_secret_supabase_key(self):
        with self.assertRaisesRegex(ValueError, "publishable"):
            build_pages(
                self.source,
                self.output,
                "https://example.supabase.co",
                "sb_secret_do_not_use",
            )

    def test_build_rejects_legacy_service_role_jwt(self):
        payload = base64.urlsafe_b64encode(
            json.dumps({"role": "service_role"}).encode("utf-8")
        ).decode("ascii").rstrip("=")
        service_key = f"header.{payload}.signature"

        self.assertFalse(is_publishable_key(service_key))
        with self.assertRaisesRegex(ValueError, "publishable"):
            build_pages(
                self.source,
                self.output,
                "https://example.supabase.co",
                service_key,
            )

    def test_build_accepts_legacy_anon_jwt(self):
        payload = base64.urlsafe_b64encode(
            json.dumps({"role": "anon"}).encode("utf-8")
        ).decode("ascii").rstrip("=")
        self.assertTrue(is_publishable_key(f"header.{payload}.signature"))

    def test_build_requires_project_configuration(self):
        with self.assertRaisesRegex(ValueError, "SUPABASE_URL"):
            build_pages(self.source, self.output, "", "")


if __name__ == "__main__":
    unittest.main()
