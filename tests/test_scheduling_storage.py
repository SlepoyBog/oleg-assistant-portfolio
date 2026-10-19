import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import Storage


root = Path(__file__).resolve().parent / ".tmp_scheduling_storage"
shutil.rmtree(root, ignore_errors=True)
root.mkdir(parents=True)
try:
    storage = Storage(root / "assistant.db")
    storage.initialize()
    entry_id, _ = storage.add_entry("task", "Проверить труборез")

    assert storage.set_task_duration(entry_id, 45)
    task = storage.entry(entry_id)
    assert task["duration_minutes"] == 45
    assert task["scheduled_at"] is None

    assert storage.set_task_schedule(
        entry_id,
        planned_for="2026-09-30",
        scheduled_at="10:15",
        duration_minutes=45,
        locked=True,
    )
    task = storage.entry(entry_id)
    assert task["planned_for"] == "2026-09-30"
    assert task["scheduled_at"] == "10:15"
    assert task["schedule_locked"] == 1

    rows = storage.scheduling_entries()
    assert rows[0]["id"] == entry_id
    assert rows[0]["duration_minutes"] == 45

    overdue_id, _ = storage.add_entry(
        "task", "Просроченная задача", planned_for="2026-09-29"
    )
    assert storage.set_task_deadline(overdue_id, "2026-10-10")
    assert storage.carry_over_open_tasks("2026-10-01") == 1
    overdue = storage.entry(overdue_id)
    assert overdue["planned_for"] == "2026-10-01"
    assert overdue["deadline"] == "2026-10-10"
    assert storage.entry(entry_id)["planned_for"] == "2026-09-30"
finally:
    storage = None
    shutil.rmtree(root, ignore_errors=True)

print("all checks passed")
