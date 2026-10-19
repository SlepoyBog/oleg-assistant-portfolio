import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot
from telegram.error import NetworkError


class StopLoop(BaseException):
    pass


async def verify_setup_survives_network_error():
    telegram_bot = SimpleNamespace(
        set_my_commands=AsyncMock(side_effect=NetworkError("temporary failure"))
    )
    created = []
    application = SimpleNamespace(
        bot=telegram_bot,
        create_task=lambda coroutine: created.append(coroutine),
    )
    await bot.setup_bot(application)
    assert len(created) == 3
    for coroutine in created:
        coroutine.close()


async def verify_daily_monitor_retries():
    calls = 0
    sleeps = 0

    async def maintenance(_application):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise NetworkError("temporary failure")

    async def sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise StopLoop

    original_maintenance = bot.run_daily_maintenance
    original_sleep = bot.asyncio.sleep
    bot.run_daily_maintenance = maintenance
    bot.asyncio.sleep = sleep
    try:
        try:
            await bot.monitor_daily_tasks(SimpleNamespace(), interval_seconds=0)
        except StopLoop:
            pass
    finally:
        bot.run_daily_maintenance = original_maintenance
        bot.asyncio.sleep = original_sleep
    assert calls == 2


asyncio.run(verify_setup_survives_network_error())
asyncio.run(verify_daily_monitor_retries())

print("all checks passed")
