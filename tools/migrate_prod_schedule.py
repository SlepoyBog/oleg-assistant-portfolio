import sqlite3
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from storage import Storage, now


TASKS = (
    ("Складирование заготовки сразу после реза", "materials", "low"),
    ("Удалить лишние программы", "materials", "normal"),
    ("Отсортировать все позиции по программам", "materials", "normal"),
    ("Определить материал кулачков и держателей", "materials", "normal"),
    ("150 DIN приварная 168×6 ГС", "general", "normal"),
    ("Заказать папку на труборез", "purchases", "normal"),
    ("Сделать кольца/шайбы для роликов на большой труборез", "general", "normal"),
    ("Доработать кулачки на труборезе", "tasks", "high"),
)


def migrate(database_path):
    database_path = Path(database_path)
    storage = Storage(database_path)
    storage.initialize()

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        existing = {
            row["text"].strip().casefold(): row["id"]
            for row in connection.execute("SELECT id,text FROM entries")
        }

        aliases = {
            "заказать папку на труборез": "Заказать папку на труборез",
            "сделать кольца/шайбы для роликов на большой труборез": (
                "Сделать кольца/шайбы для роликов на большой труборез"
            ),
        }
        for old_text, new_text in aliases.items():
            entry_id = existing.get(old_text)
            if entry_id:
                connection.execute(
                    """UPDATE entries
                       SET kind='task',entry_type='task',text=?,original_text=?,
                           classification_status='confirmed'
                       WHERE id=?""",
                    (new_text, new_text, entry_id),
                )

        compound = connection.execute(
            "SELECT id FROM entries WHERE id=18"
        ).fetchone()
        if compound:
            first_text = TASKS[0][0]
            connection.execute(
                """UPDATE entries
                   SET kind='task',entry_type='task',text=?,original_text=?,
                       category='materials',priority='low',
                       classification_status='confirmed'
                   WHERE id=18""",
                (first_text, first_text),
            )

        current = {
            row["text"].strip().casefold(): row["id"]
            for row in connection.execute("SELECT id,text FROM entries")
        }
        for text, category, priority in TASKS:
            key = text.casefold()
            entry_id = current.get(key)
            if entry_id is None:
                cursor = connection.execute(
                    """INSERT INTO entries(
                         kind,text,category,created_at,status,entry_type,
                         classification_status,priority,original_text
                       ) VALUES('task',?,?,?,'open','task','confirmed',?,?)""",
                    (text, category, now(), priority, text),
                )
                entry_id = cursor.lastrowid
                current[key] = entry_id
            else:
                connection.execute(
                    """UPDATE entries
                       SET kind='task',entry_type='task',category=?,priority=?,
                           classification_status='confirmed'
                       WHERE id=?""",
                    (category, priority, entry_id),
                )

        connection.execute(
            """UPDATE entries SET priority='normal'
               WHERE entry_type='task' AND (priority IS NULL OR priority='')"""
        )

    return storage


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: migrate_prod_schedule.py DATABASE_PATH")
    migrated = migrate(sys.argv[1])
    print(f"open scheduling entries: {len(migrated.scheduling_entries())}")
