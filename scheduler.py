from dataclasses import dataclass
from datetime import date, datetime, timedelta


WORK_START = 8 * 60
WORK_END = 17 * 60
LUNCH_START = 12 * 60 + 30
LUNCH_END = 13 * 60 + 30
SLOT_MINUTES = 15


@dataclass(frozen=True)
class Allocation:
    entry_id: int
    planned_for: date
    scheduled_at: str
    duration_minutes: int
    locked: bool = False


@dataclass(frozen=True)
class ScheduleResult:
    allocations: dict
    unscheduled: dict


class Planner:
    """Places open tasks into contiguous free work slots for one work week."""

    def build(self, entries, week_start, not_before=None):
        week_start = self._as_date(week_start)
        not_before = self._as_date(not_before) or week_start
        workdays = [
            week_start + timedelta(days=offset)
            for offset in range(8 * 7)
            if (week_start + timedelta(days=offset)).weekday() < 5
        ]
        busy = {day: set(self._lunch_slots()) for day in workdays}
        allocations = {}
        unscheduled = {}

        for entry in entries:
            if entry["entry_type"] == "event":
                self._reserve_fixed(entry, busy)
            elif entry["entry_type"] == "task" and entry.get("schedule_locked"):
                allocation = self._reserve_fixed(entry, busy, locked=True)
                if allocation:
                    allocations[entry["id"]] = allocation

        tasks = [
            entry for entry in entries
            if entry["entry_type"] == "task" and not entry.get("schedule_locked")
        ]
        tasks.sort(key=self._task_order)

        for entry in tasks:
            duration = entry.get("duration_minutes")
            if not duration or duration <= 0:
                unscheduled[entry["id"]] = "Не указана длительность"
                continue
            slot_count = (duration + SLOT_MINUTES - 1) // SLOT_MINUTES
            earliest = self._as_date(entry.get("planned_for")) or not_before
            earliest = max(earliest, not_before)
            allocation = self._find_slot(entry["id"], slot_count, duration, workdays, busy, earliest)
            if allocation:
                allocations[entry["id"]] = allocation
            else:
                unscheduled[entry["id"]] = "Нет свободного непрерывного интервала"

        return ScheduleResult(allocations, unscheduled)

    @staticmethod
    def _as_date(value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))

    @staticmethod
    def _minutes(value):
        hours, minutes = map(int, value.split(":"))
        return hours * 60 + minutes

    @staticmethod
    def _time_text(minutes):
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    @staticmethod
    def _lunch_slots():
        return range(LUNCH_START, LUNCH_END, SLOT_MINUTES)

    @staticmethod
    def _task_order(entry):
        priority_order = {"high": 0, "normal": 1, "low": 2}
        planned = entry.get("planned_for") or "9999-12-31"
        return priority_order.get(entry.get("priority"), 1), planned, entry["id"]

    def _reserve_fixed(self, entry, busy, locked=False):
        planned = self._as_date(entry.get("planned_for"))
        start_text = entry.get("scheduled_at")
        if planned not in busy or not start_text:
            return None
        duration = entry.get("duration_minutes") or SLOT_MINUTES
        start = self._minutes(start_text)
        slots = range(start, start + duration, SLOT_MINUTES)
        busy[planned].update(slots)
        if locked:
            return Allocation(entry["id"], planned, start_text, duration, True)
        return None

    def _find_slot(self, entry_id, slot_count, duration, workdays, busy, earliest):
        for day in workdays:
            if day < earliest:
                continue
            for start in range(WORK_START, WORK_END, SLOT_MINUTES):
                slots = [start + offset * SLOT_MINUTES for offset in range(slot_count)]
                if slots[-1] + SLOT_MINUTES > WORK_END:
                    break
                if any(slot in busy[day] for slot in slots):
                    continue
                busy[day].update(slots)
                return Allocation(
                    entry_id,
                    day,
                    self._time_text(start),
                    duration,
                    False,
                )
        return None
