import shutil
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook

from plan_exporter import export_management_plan
from storage import Storage


project = Path(__file__).resolve().parents[1]
root = Path(__file__).resolve().parent / ".tmp_bundled_plan_template"
shutil.rmtree(root, ignore_errors=True)
root.mkdir(parents=True)
try:
    storage = Storage(root / "assistant.db")
    storage.initialize()
    task_id, _ = storage.add_entry("task", "Проверка встроенного шаблона")
    storage.set_task_duration(task_id, 30)

    target = root / "plan.xlsx"
    export_management_plan(
        storage,
        project / "templates" / "weekly-plan.xlsx",
        target,
        today=date(2026, 9, 30),
    )

    workbook = load_workbook(target)
    sheet = workbook["Мой распорядок"]
    assert sheet["D4"].value == "Проверка встроенного шаблона"
    assert sheet["A58"].value == task_id
    assert sheet["H58"].value == "Нет"
    assert sheet["H58"].data_type == "s"
    assert len(sheet.data_validations.dataValidation) == 1
    workbook.close()
finally:
    storage = None
    shutil.rmtree(root, ignore_errors=True)

print("all checks passed")
