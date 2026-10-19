import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot
from network_watchdog import ConnectivityWatchdog, PendingUpdateWatchdog
from telegram.error import NetworkError


application = SimpleNamespace(stop_running=Mock())
context = SimpleNamespace(
    error=NetworkError("temporary connection failure"),
    application=application,
)

bot.network_watchdog.reset()
for _ in range(bot.NETWORK_ERROR_LIMIT - 1):
    asyncio.run(bot.handle_error(None, context))
assert application.stop_running.call_count == 0

asyncio.run(bot.handle_error(None, context))
assert application.stop_running.call_count == 1

bot.network_watchdog.reset()
application.stop_running.reset_mock()
asyncio.run(bot.mark_update_received(None, None))
asyncio.run(bot.handle_error(None, context))
assert application.stop_running.call_count == 0

pending_watchdog = PendingUpdateWatchdog(limit=2)
assert not pending_watchdog.observe(1)
assert pending_watchdog.observe(1)
assert not pending_watchdog.observe(0)
assert not pending_watchdog.observe(1)

connectivity_watchdog = ConnectivityWatchdog(limit=3)
assert not connectivity_watchdog.failed()
assert not connectivity_watchdog.failed()
assert connectivity_watchdog.failed()
connectivity_watchdog.succeeded()
assert not connectivity_watchdog.failed()


class FailingProbe:
    calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get_webhook_info(self):
        self.calls += 1
        raise NetworkError("probe failed")


async def no_sleep(_):
    return None


probe = FailingProbe()
probe_application = SimpleNamespace(stop_running=Mock())
original_bot = bot.Bot
original_sleep = bot.asyncio.sleep
original_connectivity_watchdog = bot.connectivity_watchdog
bot.Bot = lambda *args, **kwargs: probe
bot.asyncio.sleep = no_sleep
bot.connectivity_watchdog = ConnectivityWatchdog(limit=3)
try:
    asyncio.run(bot.monitor_pending_updates(probe_application, interval_seconds=0))
finally:
    bot.Bot = original_bot
    bot.asyncio.sleep = original_sleep
    bot.connectivity_watchdog = original_connectivity_watchdog

assert probe.calls == 3
assert probe_application.stop_running.call_count == 1

print("all checks passed")
