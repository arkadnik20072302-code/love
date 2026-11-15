import base64
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
OUTPUT_DIR = ROOT / "dist"
EXCLUDED_FILES = {"app.js", "login.js", "memories-app.js", "stories-app.js", "music-app.js"}


def build_pages(
    source_dir=STATIC_DIR,
    output_dir=OUTPUT_DIR,
    supabase_url=None,
    publishable_key=None,
):
    supabase_url = (supabase_url or os.environ.get("SUPABASE_URL", "")).strip().rstrip("/")
    publishable_key = (
        publishable_key
        or os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    ).strip()
    parsed_url = urlparse(supabase_url)
    if parsed_url.scheme != "https" or not parsed_url.hostname or not publishable_key:
        raise ValueError(
            "Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY as environment variables."
        )
    if not is_publishable_key(publishable_key):
        raise ValueError("A publishable Supabase key is required; secret keys are not allowed.")

    asset_version = os.environ.get("GITHUB_SHA", "dev").strip()[:12] or "dev"
    source_dir = Path(source_dir)
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    for path in source_dir.rglob("*"):
        if not path.is_file() or path.name.lower() in EXCLUDED_FILES:
            continue
        relative = path.relative_to(source_dir)
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)

    page_scripts = {
        "index.html": (
            '<script src="/app.js" defer></script>',
            f'<script src="./pages-config.js?v={asset_version}"></script>\n  <script type="module" src="./pages-app.js?v={asset_version}"></script>',
        ),
        "login.html": (
            '<script src="/login.js" defer></script>',
            f'<script src="./pages-config.js?v={asset_version}"></script>\n  <script type="module" src="./pages-login.js?v={asset_version}"></script>',
        ),
        "memories.html": (
            '<script type="module" src="./memories-app.js"></script>',
            f'<script src="./pages-config.js?v={asset_version}"></script>\n  <script type="module" src="./pages-memories-app.js?v={asset_version}"></script>',
        ),
        "stories.html": (
            '<script type="module" src="./stories-app.js"></script>',
            f'<script src="./pages-config.js?v={asset_version}"></script>\n  <script type="module" src="./pages-stories-app.js?v={asset_version}"></script>',
        ),
    }
    asset_replacements = {
        'href="/style.css"': 'href="./style.css"',
        'href="/login.css"': 'href="./login.css"',
        'src="/theme.js"': 'src="./theme.js"',
        'src="/photo1.jpg"': 'src="./photo1.jpg"',
    }
    for html_name, (old_script, new_script) in page_scripts.items():
        html_path = output_dir / html_name
        html = html_path.read_text(encoding="utf-8")
        if html.count(old_script) != 1:
            raise ValueError(f"Expected exactly one local script tag in {html_name}.")
        html = html.replace(old_script, new_script)
        if html_name != "login.html":
            music_script = '<script type="module" src="./music-app.js"></script>'
            pages_music_script = (
                f'<script type="module" src="./pages-music-app.js?v={asset_version}"></script>'
            )
            if html.count(music_script) != 1:
                raise ValueError(f"Expected exactly one music script tag in {html_name}.")
            html = html.replace(music_script, pages_music_script)
        for old, new in asset_replacements.items():
            html = html.replace(old, new)
        for asset in ("style.css", "login.css", "theme.js", "photo1.jpg"):
            html = html.replace(
                f'"./{asset}"',
                f'"./{asset}?v={asset_version}"',
            )
        html_path.write_text(html, encoding="utf-8", newline="\n")

    for script_name in (
        "pages-app.js",
        "pages-login.js",
        "pages-client.js",
        "pages-memories-app.js",
        "pages-stories-app.js",
        "pages-music-app.js",
        "music-player.js",
        "memory-gallery.js",
    ):
        script_path = output_dir / script_name
        script = script_path.read_text(encoding="utf-8")
        for asset in (
            "pages-client.js",
            "stories-page.js",
            "memory-gallery.js",
            "photo-utils.js",
            "music-player.js",
        ):
            script = script.replace(
                f'"./{asset}"',
                f'"./{asset}?v={asset_version}"',
            )
        script_path.write_text(script, encoding="utf-8", newline="\n")

    config = json.dumps(
        {"url": supabase_url, "publishableKey": publishable_key},
        ensure_ascii=True,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    (output_dir / "pages-config.js").write_text(
        f"window.WISHLIST_CONFIG = {config};\n",
        encoding="utf-8",
        newline="\n",
    )
    return output_dir


def is_publishable_key(key):
    if key.startswith("sb_publishable_"):
        return True
    if key.startswith("sb_secret_") or "service_role" in key.lower():
        return False
    parts = key.split(".")
    if len(parts) != 3:
        return False
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return False
    return isinstance(claims, dict) and claims.get("role") == "anon"


if __name__ == "__main__":
    build_pages()
