import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import Storage

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    storage = Storage(root / "assistant.db")
    storage.initialize()
    entry_id, category = storage.add_entry("note", "Размер тары 1140х740")
    assert category == "dimensions"
    ok, new_category = storage.edit_entry(entry_id, "Станок Longxin T28")
    assert ok and new_category == "machines"
    storage.save_card("part", "Корпус 150_125", {
        "machine": "Longxin T28", "material": "09Г2С", "thickness": "8 мм"
    })
    assert storage.find_card("150_125")["machine"] == "Longxin T28"
    assert storage.inventory_change("защитное стекло", 20)["quantity"] == 20
    assert storage.inventory_change("защитное стекло", -2, "Longxin T28")["quantity"] == 18
    storage.set_minimum("защитное стекло", 5)
    history = storage.inventory_history("защитное стекло")
    assert [row["delta"] for row in history] == [-2, 20]
    assert history[0]["machine"] == "Longxin T28"
    backup = storage.backup(root / "backups")
    assert backup.exists()
    restored = Storage(backup)
    assert restored.inventory_find("защитное стекло")[0]["quantity"] == 18
    assert storage.delete_entry(entry_id)

    inbox_id, _ = storage.add_entry(
        "inbox", "Станок снова шумит", classification_status="inbox"
    )
    inbox = storage.inbox_entries()
    assert inbox[0]["id"] == inbox_id
    assert storage.entry(inbox_id)["original_text"] == "Станок снова шумит"
    assert storage.reclassify(inbox_id, "task", planned_for="2026-09-30")
    assert storage.entry(inbox_id)["entry_type"] == "task"
    assert storage.plan_entries("2026-09-30")[0]["id"] == inbox_id

    event_id, _ = storage.add_entry(
        "event", "Приедет поставщик", planned_for="2026-10-01", scheduled_at="10:00"
    )
    assert storage.plan_entries("2026-10-01")[0]["id"] == event_id
    undated_id, _ = storage.add_entry("task", "Старая задача без даты")
    assert undated_id in {
        row["id"] for row in storage.plan_entries("2026-10-01")
    }
print("all checks passed")
