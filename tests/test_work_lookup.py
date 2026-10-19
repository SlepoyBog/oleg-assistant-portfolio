import tempfile
from pathlib import Path
import sys

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from work_lookup import WorkLookup


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
    root = Path(directory)
    saw = root / "Таблицы распила" / "Актуальные"
    modes = root / "Режимы резки" / "Режимы резки Пдф"
    saw.mkdir(parents=True)
    modes.mkdir(parents=True)
    (saw / "18 ТАБЛИЦА РАСПИЛА ЗАГОТОВКИ ЦПП 07.09.2026.pdf").touch()
    (modes / "Режимы резки ст20 резка ТК-2.pdf").touch()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ст20"
    sheet.append([f"Колонка {index}" for index in range(26)])
    row = [None] * 26
    row[0], row[1], row[4], row[5] = "Патрубок Ду15/10", "Ст20", "2.8мм", "2мм"
    row[6], row[9], row[10], row[13], row[24] = 9.3, 10, 45, -2, "Утверждено"
    sheet.append(row)
    workbook.save(root / "Режимы резки" / "база.xlsx")

    lookup = WorkLookup(root)
    saw_result = lookup.saw_tables("ЦПП")
    assert len(saw_result) == 1
    assert saw_result[0]["date"] == "07.09.2026"
    assert lookup.cutting_sources("Ст20 резка")[0]["status"] == "утверждено"
    mode = lookup.cutting_rows("Ст20 Ду15/10")[0]
    assert mode["speed"] == 9.3
    assert mode["status"] == "Утверждено"

print("all checks passed")
