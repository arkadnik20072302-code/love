import datetime
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app


def main():
    config = app.supabase_config()
    if config is None:
        raise app.SupabaseError(
            "В этом PowerShell не заданы SUPABASE_URL и "
            "SUPABASE_SECRET_KEY/SUPABASE_SERVICE_ROLE_KEY."
        )

    _, key = config
    if key.startswith("sb_secret_"):
        key_type = "новый sb_secret_"
    elif key.startswith("sb_publishable_"):
        key_type = "публичный sb_publishable_ (не подходит для backend)"
    elif key.count(".") == 2:
        key_type = "legacy JWT (service_role)"
    else:
        key_type = "неизвестный формат (значение не показывается)"
    print(f"URL задан; ключ: {key_type}.")

    for table in ("settings", "wishes"):
        try:
            rows = app.supabase_request("GET", f"{table}?select=id")
        except app.SupabaseError as exc:
            raise app.SupabaseError(f"Проверка таблицы {table}: {exc}") from exc
        print(f"Таблица {table}: доступна через Data API; строк: {len(rows)}.")

    wish_id = uuid.uuid4().hex
    title = f"Проверка сохранения {wish_id[:8]}"
    payload = {
        "id": wish_id,
        "title": title,
        "category": "Проверка",
        "status": "planned",
        "created_at": datetime.date.today().isoformat(),
    }
    try:
        created = app.supabase_request(
            "POST",
            "wishes",
            payload,
            prefer="return=representation",
        )
        if not any(row.get("id") == wish_id for row in created):
            raise app.SupabaseError("Supabase не подтвердил создание тестовой идеи.")

        found = app.supabase_request(
            "GET",
            f"wishes?select=id,title&id=eq.{wish_id}",
        )
        if not any(row.get("id") == wish_id and row.get("title") == title for row in found):
            raise app.SupabaseError("Тестовая идея не найдена при повторном чтении из Supabase.")

        deleted = app.supabase_request(
            "DELETE",
            f"wishes?id=eq.{wish_id}",
            prefer="return=representation",
        )
        if not any(row.get("id") == wish_id for row in deleted):
            raise app.SupabaseError("Supabase не подтвердил удаление тестовой идеи.")

        remaining = app.supabase_request(
            "GET",
            f"wishes?select=id&id=eq.{wish_id}",
        )
        if any(row.get("id") == wish_id for row in remaining):
            raise app.SupabaseError("Тестовая идея осталась в Supabase после удаления.")
    except app.SupabaseError as exc:
        try:
            app.supabase_request(
                "DELETE",
                f"wishes?id=eq.{wish_id}",
                prefer="return=representation",
            )
        except app.SupabaseError as cleanup_error:
            raise app.SupabaseError(
                f"Проверка сохранения не пройдена: {exc}; не удалось удалить "
                f"тестовую запись: {cleanup_error}"
            ) from exc
        raise

    print("Тестовая идея создана, прочитана и удалена; запись в Supabase подтверждена.")


if __name__ == "__main__":
    try:
        main()
    except app.SupabaseError as exc:
        print(f"Проверка не пройдена: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
