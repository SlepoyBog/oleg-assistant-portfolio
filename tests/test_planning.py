from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import capture_kind, planning_fields


today = date(2026, 9, 29)

assert capture_kind("нужно заказать папку")[0] == "task"
assert capture_kind("Поставщик работает по предоплате")[0] == "inbox"
assert capture_kind("идея: поставить датчик")[0] == "idea"
assert capture_kind("В четверг в 10:00 приедет поставщик")[0] == "event"

assert planning_fields("сделать завтра", today) == ("2026-09-30", None)
assert planning_fields("в четверг в 10:00", today) == ("2026-10-01", "10:00")
assert planning_fields("05.10 в 9:15", today) == ("2026-10-05", "09:15")

print("all checks passed")
