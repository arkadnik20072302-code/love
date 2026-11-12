import argparse
import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app


def migrate_request(step, method, resource, payload=None, prefer=None):
    try:
        return app.supabase_request(method, resource, payload, prefer)
    except app.SupabaseError as exc:
        raise app.SupabaseError(f"Шаг «{step}»: {exc}") from exc


def migrate():
    if not app.using_supabase():
        raise app.SupabaseError(
            "Сначала задайте SUPABASE_URL и SUPABASE_SECRET_KEY "
            "(или legacy SUPABASE_SERVICE_ROLE_KEY)."
        )

    migrate_request(
        "проверка таблицы settings",
        "GET",
        "settings?select=id&limit=1",
    )
    migrate_request(
        "проверка таблицы wishes",
        "GET",
        "wishes?select=id&limit=1",
    )

    with app.DB_LOCK, app.database() as db:
        settings = dict(db.execute(
            "SELECT id, together_since, demob_date FROM settings WHERE id = 1"
        ).fetchone())
        local_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(wishes)").fetchall()
        }
        planned_time_column = "planned_time" if "planned_time" in local_columns else "'' AS planned_time"
        completed_time_column = "completed_time" if "completed_time" in local_columns else "'' AS completed_time"
        wishes = [
            dict(row)
            for row in db.execute(
                "SELECT id, title, category, status, created_at, planned_for, "
                f"{planned_time_column}, completed_at, {completed_time_column}, "
                "photo FROM wishes ORDER BY created_at, id"
            ).fetchall()
        ]
        reminders = [
            dict(row)
            for row in db.execute(
                "SELECT id, title, remind_at, created_by, sent, sent_to "
                "FROM reminders ORDER BY remind_at, id"
            ).fetchall()
        ]

    settings_payload = {
        "id": settings["id"],
        "together_since": settings["together_since"] or None,
        "demob_date": settings["demob_date"] or None,
    }
    migrate_request(
        "перенос дат",
        "POST",
        "settings?on_conflict=id",
        settings_payload,
        prefer="resolution=merge-duplicates,return=representation",
    )

    for wish in wishes:
        for field in ("planned_for", "planned_time", "completed_at", "completed_time"):
            wish[field] = wish[field] or None
        migrate_request(
            f"перенос желания «{wish['title'][:60]}»",
            "POST",
            "wishes?on_conflict=id",
            wish,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    for reminder in reminders:
        reminder["sent"] = bool(reminder["sent"])
        reminder["sent_to"] = reminder["sent_to"] or ""
        migrate_request(
            f"перенос напоминания «{reminder['title'][:60]}»",
            "POST",
            "reminders?on_conflict=id",
            reminder,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    print(
        f"Перенесены настройки, {len(wishes)} желаний и "
        f"{len(reminders)} напоминаний в Supabase."
    )
    print("Локальная SQLite-база оставлена без изменений.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Копирует локальную SQLite-базу в Supabase.")
    parser.parse_args()
    try:
        migrate()
    except (app.SupabaseError, OSError) as exc:
        print(f"Миграция не выполнена: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
