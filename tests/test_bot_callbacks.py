import asyncio
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot
from storage import Storage
from telegram.constants import ChatType


class FixedTemporaryDirectory:
    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.path = Path(__file__).resolve().parent / ".tmp_bot_callbacks"

    def __enter__(self):
        shutil.rmtree(self.path, ignore_errors=True)
        self.path.mkdir(parents=True)
        return str(self.path)

    def __exit__(self, exc_type, exc, traceback):
        shutil.rmtree(self.path, ignore_errors=True)


tempfile.TemporaryDirectory = FixedTemporaryDirectory


class Query:
    def __init__(self, data):
        self.data = data
        self.answered = False
        self.text = None
        self.reply_markup = None

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup


def owner_update(data):
    query = Query(data)
    update = SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
        effective_user=SimpleNamespace(id=12345),
        effective_message=object(),
        message=None,
    )
    return update, query


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
    planned_date = (date.today() + timedelta(days=7)).isoformat()
    storage = Storage(Path(directory) / "assistant.db")
    storage.initialize()
    storage.set_setting("owner_id", 12345)
    task_id, _ = storage.add_entry("task", "Проверить кнопку")
    duration_task_id, _ = storage.add_entry("task", "Запланировать по длительности")
    delete_task_id, _ = storage.add_entry("task", "Удалить через подтверждение")
    item = storage.inventory_change("защитное стекло", 3)
    inbox_id, _ = storage.add_entry(
        "inbox", "Проверить шум завтра в 10:30", classification_status="inbox"
    )

    original_store = bot.store
    original_refresh = bot.refresh_management_plan
    bot.store = storage
    bot.refresh_management_plan = lambda today=None: False
    try:
        update, query = owner_update(f"task_done:{task_id}")
        asyncio.run(bot.task_button(update, None))
        assert query.answered
        assert query.text == f"Дело #{task_id} выполнено."
        assert storage.entry(task_id)["status"] == "done"

        update, query = owner_update(f"task_date:{planned_date}:{duration_task_id}")
        asyncio.run(bot.task_date_decision(update, None))
        dated = storage.entry(duration_task_id)
        assert query.answered
        assert dated["planned_for"] == planned_date
        assert "приоритет" in query.text.casefold()

        update, query = owner_update(f"task_priority:high:{duration_task_id}")
        asyncio.run(bot.task_priority_decision(update, None))
        prioritized = storage.entry(duration_task_id)
        assert query.answered
        assert prioritized["priority"] == "high"
        assert "крайний срок" in query.text.casefold()

        update, query = owner_update(f"task_deadline:none:{duration_task_id}")
        asyncio.run(bot.task_deadline_decision(update, None))
        assert query.answered
        assert storage.entry(duration_task_id)["deadline"] is None
        assert "Сколько времени потребуется" in query.text

        update, query = owner_update(f"task_duration:45:{duration_task_id}")
        asyncio.run(bot.task_duration_decision(update, None))
        scheduled = storage.entry(duration_task_id)
        assert query.answered
        assert scheduled["duration_minutes"] == 45
        assert scheduled["planned_for"] == planned_date
        assert scheduled["scheduled_at"] is not None
        assert "Запланировал" in query.text

        update, query = owner_update(f"task_delete:{delete_task_id}")
        asyncio.run(bot.task_button(update, None))
        assert query.answered
        assert "Удалить дело" in query.text
        assert storage.entry(delete_task_id) is not None

        update, query = owner_update(f"task_delete_confirm:{delete_task_id}")
        asyncio.run(bot.task_delete_decision(update, None))
        assert query.answered
        assert storage.entry(delete_task_id) is None

        update, query = owner_update(f"stock_history:{item['id']}")
        asyncio.run(bot.inventory_button(update, None))
        assert query.answered
        assert "защитное стекло" in query.text
        assert "+3" in query.text

        update, query = owner_update(f"stock_adjust:{item['id']}:-1")
        asyncio.run(bot.inventory_button(update, None))
        assert query.answered
        assert "Подтвердите" in query.text
        assert storage.inventory_item(item["id"])["quantity"] == 3

        update, query = owner_update(f"stock_adjust_confirm:{item['id']}:-1")
        asyncio.run(bot.inventory_adjust_decision(update, None))
        assert query.answered
        assert storage.inventory_item(item["id"])["quantity"] == 2
        assert "Остаток" in query.text

        update, query = owner_update(f"entry_kind:task:{inbox_id}")
        asyncio.run(bot.entry_kind_decision(update, None))
        assert query.answered
        assert "задача" in query.text
        converted = storage.entry(inbox_id)
        assert converted["entry_type"] == "task"
        assert converted["classification_status"] == "confirmed"
        assert converted["scheduled_at"] == "10:30"
    finally:
        bot.store = original_store
        bot.refresh_management_plan = original_refresh

print("all checks passed")
