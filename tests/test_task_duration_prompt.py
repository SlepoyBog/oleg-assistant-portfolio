import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot
from storage import Storage
from telegram.constants import ChatType

assert bot.parse_duration_minutes("3 часа") == 180
assert bot.parse_duration_minutes("75 минут") == 75
assert bot.parse_duration_minutes("1,5 часа") == 90


class Message:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


class Query:
    def __init__(self, data):
        self.data = data
        self.text = None

    async def answer(self):
        return None

    async def edit_message_text(self, text, reply_markup=None):
        self.text = text


root = Path(__file__).resolve().parent / ".tmp_task_duration_prompt"
shutil.rmtree(root, ignore_errors=True)
root.mkdir(parents=True)
try:
    storage = Storage(root / "assistant.db")
    storage.initialize()
    storage.set_setting("owner_id", 12345)
    message = Message("задача: Проверить труборез")
    update = SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
        effective_user=SimpleNamespace(id=12345),
    )
    context = SimpleNamespace(user_data={})

    original_store = bot.store
    bot.store = storage
    try:
        asyncio.run(bot.message(update, context))
    finally:
        bot.store = original_store

    tasks = storage.list_entries("task")
    assert len(tasks) == 1
    task = storage.entry(tasks[0]["id"])
    assert task["duration_minutes"] is None
    assert "На какой день" in message.replies[-1][0]
    assert message.replies[-1][1] is not None

    query = Query(f"task_date:custom:{task['id']}")
    callback_update = SimpleNamespace(
        callback_query=query,
        effective_message=object(),
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
        effective_user=SimpleNamespace(id=12345),
        message=None,
    )
    bot.store = storage
    try:
        asyncio.run(bot.task_date_decision(callback_update, None))
        assert "05.10" in query.text

        date_message = Message("05.10.2026")
        date_update = SimpleNamespace(
            message=date_message,
            effective_message=date_message,
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_user=SimpleNamespace(id=12345),
        )
        asyncio.run(bot.message(date_update, context))
    finally:
        bot.store = original_store

    task = storage.entry(task["id"])
    assert task["planned_for"] == "2026-10-05"
    assert "приоритет" in date_message.replies[-1][0].casefold()
    assert date_message.replies[-1][1] is not None
finally:
    storage = None
    shutil.rmtree(root, ignore_errors=True)

print("all checks passed")
