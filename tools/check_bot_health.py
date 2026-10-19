"""Check whether Telegram is reachable and the bot drains incoming updates.

The token is read from .env and is never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values


def read_config(project: Path) -> tuple[str, str | None]:
    config = dotenv_values(project / ".env")
    token = config.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    proxy = config.get("TELEGRAM_PROXY") or None
    return token, proxy


def get_status(token: str, proxy: str | None) -> dict[str, object]:
    started = time.monotonic()
    with httpx.Client(proxy=proxy, timeout=10.0) as client:
        response = client.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
        response.raise_for_status()
        payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram API returned ok=false")
    result = payload["result"]
    return {
        "api_reachable": True,
        "pending_update_count": int(result.get("pending_update_count", 0)),
        "response_seconds": round(time.monotonic() - started, 2),
        "transport": "proxy" if proxy else "direct",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--proxy", help="Temporarily override TELEGRAM_PROXY")
    args = parser.parse_args()

    try:
        token, proxy = read_config(args.project)
        if args.proxy:
            proxy = args.proxy
        samples = []
        for index in range(args.samples):
            samples.append(get_status(token, proxy))
            if index + 1 < args.samples:
                time.sleep(args.interval)
    except Exception as error:
        print(json.dumps({"healthy": False, "error": type(error).__name__}))
        return 2

    pending = [sample["pending_update_count"] for sample in samples]
    draining = len(pending) < 2 or pending[-1] < pending[0] or pending[-1] == 0
    verdict = {
        "healthy": draining,
        "samples": samples,
        "reason": "queue_draining_or_empty" if draining else "queue_not_draining",
    }
    print(json.dumps(verdict, ensure_ascii=False))
    return 0 if draining else 1


if __name__ == "__main__":
    sys.exit(main())
