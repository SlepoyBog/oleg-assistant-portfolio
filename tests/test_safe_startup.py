from pathlib import Path
from unittest.mock import Mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot
from telegram.error import InvalidToken


def invalid_token_start():
    raise InvalidToken("secret-token-must-not-be-logged")


critical = Mock()
original = bot.logger.critical
bot.logger.critical = critical
try:
    assert bot.run(invalid_token_start) == 2
finally:
    bot.logger.critical = original

message = critical.call_args.args[0]
assert "secret-token-must-not-be-logged" not in message
assert "Telegram" in message

print("all checks passed")
