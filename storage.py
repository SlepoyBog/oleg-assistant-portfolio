import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

CATEGORY_NAMES = {
    "machines": "станки", "parts": "детали", "dimensions": "размеры",
    "materials": "материалы", "tooling": "оснастка", "purchases": "закупки",
    "cutting_modes": "режимы", "processes": "техпроцессы",
    "tasks": "дела", "general": "общее",
}


class Storage:
    def __init__(self, path):
        self.path = Path(path)

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextmanager
    def connect(self):
        connection = self._open()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS settings(
              key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entries(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL CHECK(kind IN ('note','task')),
              text TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open',
              created_at TEXT NOT NULL,
              completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS cards(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              card_type TEXT NOT NULL CHECK(card_type IN ('part','machine')),
              name TEXT NOT NULL COLLATE NOCASE,
              machine TEXT,
              material TEXT,
              thickness TEXT,
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(card_type,name)
            );
            CREATE TABLE IF NOT EXISTS inventory(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL COLLATE NOCASE UNIQUE,
              quantity REAL NOT NULL DEFAULT 0,
              min_quantity REAL NOT NULL DEFAULT 0,
              unit TEXT NOT NULL DEFAULT 'шт',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inventory_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              item_id INTEGER NOT NULL,
              delta REAL NOT NULL,
              machine TEXT,
              note TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(item_id) REFERENCES inventory(id)
            );
            """)
            columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(entries)"
                ).fetchall()
            }
            if "category" not in columns:
                connection.execute(
                    "ALTER TABLE entries ADD COLUMN category TEXT NOT NULL DEFAULT 'general'"
                )
                for row in connection.execute(
                    "SELECT id,text,kind FROM entries"
                ).fetchall():
                    connection.execute(
                        "UPDATE entries SET category=? WHERE id=?",
                        (classify(row["text"], row["kind"]), row["id"]),
                    )
            columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(entries)"
                ).fetchall()
            }
            migrations = (
                ("entry_type", "TEXT NOT NULL DEFAULT 'note'"),
                ("classification_status", "TEXT NOT NULL DEFAULT 'confirmed'"),
                ("planned_for", "TEXT"),
                ("scheduled_at", "TEXT"),
                ("duration_minutes", "INTEGER"),
                ("schedule_locked", "INTEGER NOT NULL DEFAULT 0"),
                ("priority", "TEXT NOT NULL DEFAULT 'normal'"),
                ("deadline", "TEXT"),
                ("original_text", "TEXT"),
            )
            for name, declaration in migrations:
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE entries ADD COLUMN {name} {declaration}"
                    )
            connection.execute(
                "UPDATE entries SET entry_type=kind "
                "WHERE entry_type='note' AND kind='task'"
            )
            connection.execute(
                "UPDATE entries SET original_text=text WHERE original_text IS NULL"
            )

    def get_setting(self, key):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key, value):
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def add_entry(
        self, kind, text, *, planned_for=None, scheduled_at=None,
        classification_status="confirmed", original_text=None,
    ):
        category = classify(text, kind)
        legacy_kind = "task" if kind == "task" else "note"
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO entries(
                     kind,text,category,created_at,entry_type,classification_status,
                     planned_for,scheduled_at,original_text
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    legacy_kind, text.strip(), category, now(), kind,
                    classification_status, planned_for, scheduled_at,
                    (original_text or text).strip(),
                ),
            )
            return cursor.lastrowid, category

    def list_entries(self, kind, limit=20):
        query = "SELECT id,text,category FROM entries WHERE entry_type=?"
        if kind == "task":
            query += " AND status='open'"
        query += " ORDER BY id DESC LIMIT ?"
        with self.connect() as connection:
            return connection.execute(query, (kind, limit)).fetchall()

    def inbox_entries(self, limit=20):
        with self.connect() as connection:
            return connection.execute(
                "SELECT id,text,category,created_at FROM entries "
                "WHERE classification_status='inbox' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def plan_entries(self, date_from, date_to=None):
        date_to = date_to or date_from
        with self.connect() as connection:
            return connection.execute(
                """SELECT id,entry_type,text,category,planned_for,scheduled_at,status
                   FROM entries
                   WHERE classification_status='confirmed'
                     AND entry_type IN ('task','event')
                     AND status='open'
                     AND (
                       planned_for BETWEEN ? AND ?
                       OR (entry_type='task' AND planned_for IS NULL)
                     )
                   ORDER BY planned_for IS NULL, planned_for,
                            COALESCE(scheduled_at, '99:99'), id""",
                (date_from, date_to),
            ).fetchall()

    def report_entries(self):
        with self.connect() as connection:
            return connection.execute(
                """SELECT id,entry_type,text,category,planned_for,scheduled_at,
                          duration_minutes,schedule_locked,priority,deadline,status
                   FROM entries
                   WHERE classification_status='confirmed'
                     AND entry_type IN ('task','event')
                     AND status='open'
                   ORDER BY planned_for IS NULL, planned_for,
                            COALESCE(scheduled_at, '99:99'), id"""
            ).fetchall()

    def scheduling_entries(self):
        return self.report_entries()

    def set_task_duration(self, entry_id, duration_minutes):
        duration_minutes = int(duration_minutes)
        if duration_minutes <= 0 or duration_minutes % 15:
            raise ValueError("duration must be a positive multiple of 15 minutes")
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE entries SET duration_minutes=? "
                "WHERE id=? AND entry_type='task' AND status='open'",
                (duration_minutes, entry_id),
            )
            return cursor.rowcount == 1

    def set_task_planned_for(self, entry_id, planned_for):
        date.fromisoformat(str(planned_for))
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE entries SET planned_for=?,scheduled_at=NULL,schedule_locked=0 "
                "WHERE id=? AND entry_type='task' AND status='open'",
                (str(planned_for), entry_id),
            )
            return cursor.rowcount == 1

    def set_task_priority(self, entry_id, priority):
        if priority not in {"high", "normal", "low"}:
            raise ValueError("unsupported priority")
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE entries SET priority=? "
                "WHERE id=? AND entry_type='task' AND status='open'",
                (priority, entry_id),
            )
            return cursor.rowcount == 1

    def set_task_deadline(self, entry_id, deadline):
        if deadline:
            date.fromisoformat(str(deadline))
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE entries SET deadline=? WHERE id=? AND entry_type='task' AND status='open'",
                (str(deadline) if deadline else None, entry_id),
            )
            return cursor.rowcount == 1

    def carry_over_open_tasks(self, today):
        today = str(today)
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE entries SET planned_for=?,scheduled_at=NULL
                   WHERE entry_type='task' AND status='open' AND schedule_locked=0
                     AND planned_for IS NOT NULL AND planned_for < ?""",
                (today, today),
            )
            return cursor.rowcount

    def set_task_schedule(
        self, entry_id, *, planned_for, scheduled_at, duration_minutes, locked
    ):
        duration_minutes = int(duration_minutes)
        if duration_minutes <= 0 or duration_minutes % 15:
            raise ValueError("duration must be a positive multiple of 15 minutes")
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE entries
                   SET planned_for=?,scheduled_at=?,duration_minutes=?,schedule_locked=?
                   WHERE id=? AND entry_type='task' AND status='open'""",
                (
                    planned_for,
                    scheduled_at,
                    duration_minutes,
                    1 if locked else 0,
                    entry_id,
                ),
            )
            return cursor.rowcount == 1

    def apply_schedule(self, allocations):
        with self.connect() as connection:
            for entry_id, allocation in allocations.items():
                connection.execute(
                    """UPDATE entries
                       SET planned_for=?,scheduled_at=?,duration_minutes=?
                       WHERE id=? AND entry_type='task' AND status='open'
                         AND schedule_locked=0""",
                    (
                        allocation.planned_for.isoformat(),
                        allocation.scheduled_at,
                        allocation.duration_minutes,
                        entry_id,
                    ),
                )

    def reclassify(self, entry_id, entry_type, *, planned_for=None, scheduled_at=None):
        if entry_type not in {"note", "task", "event", "idea", "log"}:
            raise ValueError("unsupported entry type")
        legacy_kind = "task" if entry_type == "task" else "note"
        with self.connect() as connection:
            row = connection.execute(
                "SELECT text FROM entries WHERE id=?", (entry_id,)
            ).fetchone()
            if not row:
                return False
            connection.execute(
                """UPDATE entries
                   SET kind=?, entry_type=?, classification_status='confirmed',
                       planned_for=?, scheduled_at=?, category=?
                   WHERE id=?""",
                (
                    legacy_kind, entry_type, planned_for, scheduled_at,
                    classify(row["text"], entry_type), entry_id,
                ),
            )
            return True

    def category_entries(self, category, limit=30):
        with self.connect() as connection:
            return connection.execute(
                "SELECT id,kind,text FROM entries WHERE category=? "
                "ORDER BY id DESC LIMIT ?",
                (category, limit),
            ).fetchall()

    def search(self, text, limit=20):
        with self.connect() as connection:
            return connection.execute(
                "SELECT id,kind,text,category FROM entries "
                "WHERE lower(text) LIKE lower(?) ORDER BY id DESC LIMIT ?",
                (f"%{text.strip()}%", limit),
            ).fetchall()

    def complete(self, entry_id):
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE entries SET status='done',completed_at=? "
                "WHERE id=? AND kind='task' AND status='open'",
                (now(), entry_id),
            )
            return cursor.rowcount == 1

    def edit_entry(self, entry_id, text):
        category = classify(text)
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE entries SET text=?,category=? WHERE id=?",
                (text.strip(), category, entry_id),
            )
            return cursor.rowcount == 1, category

    def entry(self, entry_id):
        with self.connect() as connection:
            return connection.execute(
                """SELECT id,kind,entry_type,text,category,status,
                          classification_status,planned_for,scheduled_at,
                          duration_minutes,schedule_locked,priority,deadline,original_text
                   FROM entries WHERE id=?""",
                (entry_id,),
            ).fetchone()

    def delete_entry(self, entry_id):
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM entries WHERE id=?", (entry_id,))
            return cursor.rowcount == 1

    def save_card(self, card_type, name, fields):
        timestamp = now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO cards(
                     card_type,name,machine,material,thickness,notes,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(card_type,name) DO UPDATE SET
                     machine=COALESCE(excluded.machine,cards.machine),
                     material=COALESCE(excluded.material,cards.material),
                     thickness=COALESCE(excluded.thickness,cards.thickness),
                     notes=COALESCE(excluded.notes,cards.notes),
                     updated_at=excluded.updated_at""",
                (
                    card_type, name, fields.get("machine"), fields.get("material"),
                    fields.get("thickness"), fields.get("notes"), timestamp, timestamp,
                ),
            )

    def cards(self, card_type=None):
        query = "SELECT * FROM cards"
        params = ()
        if card_type:
            query += " WHERE card_type=?"
            params = (card_type,)
        query += " ORDER BY name LIMIT 50"
        with self.connect() as connection:
            return connection.execute(query, params).fetchall()

    def find_card(self, name):
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM cards WHERE lower(name) LIKE lower(?) "
                "ORDER BY updated_at DESC LIMIT 1",
                (f"%{name.strip()}%",),
            ).fetchone()

    def inventory_change(self, name, delta, machine=None, unit="шт"):
        timestamp = now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO inventory(name,quantity,unit,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                     quantity=inventory.quantity+excluded.quantity,
                     updated_at=excluded.updated_at""",
                (name.strip(), delta, unit, timestamp),
            )
            item = connection.execute(
                "SELECT id,quantity,min_quantity,unit FROM inventory WHERE name=?",
                (name.strip(),),
            ).fetchone()
            connection.execute(
                "INSERT INTO inventory_events(item_id,delta,machine,created_at) "
                "VALUES(?,?,?,?)",
                (item["id"], delta, machine, timestamp),
            )
            return item

    def set_minimum(self, name, value):
        timestamp = now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO inventory(name,min_quantity,updated_at)
                   VALUES(?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                     min_quantity=excluded.min_quantity,
                     updated_at=excluded.updated_at""",
                (name.strip(), value, timestamp),
            )
            return connection.execute(
                "SELECT * FROM inventory WHERE name=?", (name.strip(),)
            ).fetchone()

    def inventory_list(self):
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM inventory ORDER BY name LIMIT 100"
            ).fetchall()

    def inventory_find(self, name):
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM inventory WHERE lower(name) LIKE lower(?) LIMIT 10",
                (f"%{name.strip()}%",),
            ).fetchall()

    def inventory_item(self, item_id):
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM inventory WHERE id=?", (item_id,)
            ).fetchone()

    def inventory_history(self, item, limit=20):
        if isinstance(item, int):
            condition = "inventory.id=?"
            value = item
        else:
            condition = "lower(inventory.name) LIKE lower(?)"
            value = f"%{str(item).strip()}%"
        with self.connect() as connection:
            return connection.execute(
                "SELECT inventory_events.id,inventory.name,inventory_events.delta,"
                "inventory_events.machine,inventory_events.note,"
                "inventory_events.created_at FROM inventory_events "
                "JOIN inventory ON inventory.id=inventory_events.item_id "
                f"WHERE {condition} ORDER BY inventory_events.id DESC LIMIT ?",
                (value, limit),
            ).fetchall()

    def backup(self, backup_dir, keep=20):
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"assistant-{datetime.now():%Y%m%d-%H%M%S}.db"
        source = self._open()
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        backups = sorted(backup_dir.glob("assistant-*.db"), reverse=True)
        for old in backups[keep:]:
            old.unlink()
        return target


def now():
    return datetime.now(timezone.utc).isoformat()


def classify(text, kind="note"):
    value = text.casefold()
    rules = (
        ("machines", ("станок", "longxin", "glorystar", "t28", "vs6022")),
        ("dimensions", ("размер", "габарит", "тара", "диаметр", "длина", "ширина")),
        ("tooling", ("оснаст", "приспособлен", "кондуктор", "оправк")),
        ("cutting_modes", ("режим", "сопло", "фокус", "давление", "частота", "скорость рез")),
        ("processes", ("техпроцесс", "технологическ", "операционн", "карта")),
        ("materials", ("материал", "ст20", "09г2с", "12х18", "сталь", "нержав")),
        ("parts", ("деталь", "корпус", "изготавлива", "производится")),
        ("purchases", ("купить", "заказать", "закупить", "расходник", "стекл")),
    )
    for category, keywords in rules:
        if any(keyword in value for keyword in keywords):
            return category
    return "tasks" if kind == "task" else "general"



