from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scheduler import Planner


def task(entry_id, text, duration, planned_for=None, scheduled_at=None, locked=0):
    return {
        "id": entry_id,
        "entry_type": "task",
        "text": text,
        "planned_for": planned_for,
        "scheduled_at": scheduled_at,
        "duration_minutes": duration,
        "schedule_locked": locked,
        "priority": "normal",
    }


planner = Planner()

result = planner.build(
    [
        task(1, "Первая задача", 60),
        task(2, "Вторая задача", 30),
    ],
    week_start=date(2026, 9, 28),
)

assert result.allocations[1].planned_for == date(2026, 9, 28)
assert result.allocations[1].scheduled_at == "08:00"
assert result.allocations[2].scheduled_at == "09:00"

locked = planner.build(
    [
        task(3, "Закреплённая задача", 60, "2026-09-28", "10:00", 1),
        task(4, "Автоматическая задача", 60),
    ],
    week_start=date(2026, 9, 28),
)

assert locked.allocations[3].scheduled_at == "10:00"
assert locked.allocations[3].locked
assert locked.allocations[4].scheduled_at == "08:00"

lunch = planner.build(
    [task(5, "Длинная задача", 240), task(6, "После обеда", 60)],
    week_start=date(2026, 9, 28),
)

assert lunch.allocations[5].scheduled_at == "08:00"
assert lunch.allocations[6].scheduled_at == "13:30"

midweek = planner.build(
    [task(7, "Задача, полученная в среду", 30)],
    week_start=date(2026, 9, 28),
    not_before=date(2026, 9, 30),
)
assert midweek.allocations[7].planned_for == date(2026, 9, 30)
assert midweek.allocations[7].scheduled_at == "08:00"

future_week = planner.build(
    [task(8, "Задача на следующую неделю", 30, "2026-10-05")],
    week_start=date(2026, 9, 28),
    not_before=date(2026, 10, 1),
)
assert future_week.allocations[8].planned_for == date(2026, 10, 5)
assert future_week.allocations[8].scheduled_at == "08:00"

print("all checks passed")
