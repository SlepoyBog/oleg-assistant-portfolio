from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot
from telegram.constants import ChatType


def test_callback_query_from_owner_is_authorized():
    original_owner_id = bot.owner_id
    bot.owner_id = lambda: 12345
    try:
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_user=SimpleNamespace(id=12345),
            effective_message=object(),
            message=None,
        )

        assert bot.authorized(update), "callback query from the owner must be authorized"
    finally:
        bot.owner_id = original_owner_id


if __name__ == "__main__":
    test_callback_query_from_owner_is_authorized()
    print("all checks passed")
