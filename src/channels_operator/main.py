"""Entrypoint: starts APScheduler plus the editor and publisher bots in one async runtime.

Phase 6: multi-channel runtime. All channels with a non-empty
`telegram_channel_id` run concurrently; channels with an empty target
are discovered but skipped (operator hasn't wired them up yet).

`ACTIVE_CHANNEL_ID` is now an optional filter — set it to restrict the
runtime to one channel for testing.

Phase 7's ops layer (daily summary, source health, backup) is wired
in scheduler.py.
"""

import logging
import sys
from pathlib import Path

from channels_operator.config.loader import discover_channels
from channels_operator.publishing.telegram import TelegramPublisher
from channels_operator.scheduler import log_scheduled_jobs, setup_scheduler
from channels_operator.settings import settings
from channels_operator.storage.db import init_db
from channels_operator.telegram.editor_bot import build_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


CHANNELS_DIR = Path("channels")


def main() -> int:
    init_db()

    all_channels = discover_channels(CHANNELS_DIR)
    if not all_channels:
        print(
            f"No channel configs found under {CHANNELS_DIR.resolve()}.",
            file=sys.stderr,
        )
        return 1

    logger.info(
        "Discovered %d channel config(s): %s",
        len(all_channels),
        ", ".join(sorted(all_channels.keys())),
    )

    # Optional restriction via ACTIVE_CHANNEL_ID env var
    if settings.active_channel_id:
        if settings.active_channel_id not in all_channels:
            print(
                f"ACTIVE_CHANNEL_ID='{settings.active_channel_id}' not found. "
                f"Known: {sorted(all_channels.keys())}",
                file=sys.stderr,
            )
            return 1
        candidates = {settings.active_channel_id: all_channels[settings.active_channel_id]}
        logger.info("ACTIVE_CHANNEL_ID set: filtering to %s", settings.active_channel_id)
    else:
        candidates = all_channels

    # Skip channels that don't have a telegram_channel_id wired up
    runnable: dict[str, object] = {}
    skipped: list[str] = []
    for cid, ch in candidates.items():
        if not ch.telegram_channel_id:
            skipped.append(cid)
        else:
            runnable[cid] = ch
    if skipped:
        logger.info(
            "Skipping channels with no telegram_channel_id: %s", ", ".join(skipped)
        )
    if not runnable:
        print(
            "No runnable channels (none have a telegram_channel_id set).",
            file=sys.stderr,
        )
        return 1

    # Single editor and publisher bot serve all channels (per BRIEF.md §4).
    # Per-channel tokens in channel.yaml override env vars; for v1 we
    # require *some* working token at startup.
    editor_token = settings.editor_bot_token
    publisher_token = settings.publisher_bot_token
    for ch in runnable.values():
        if ch.editor_bot_token:
            editor_token = ch.editor_bot_token  # last channel's token wins
        if ch.publisher_bot_token:
            publisher_token = ch.publisher_bot_token

    missing: list[str] = []
    if not editor_token:
        missing.append("editor bot token (EDITOR_BOT_TOKEN or any channel.yaml.editor_bot_token)")
    if not publisher_token:
        missing.append("publisher bot token (PUBLISHER_BOT_TOKEN or any channel.yaml.publisher_bot_token)")
    if not settings.operator_telegram_user_id:
        missing.append("OPERATOR_TELEGRAM_USER_ID")
    if missing:
        print("Missing required values: " + "; ".join(missing), file=sys.stderr)
        return 1

    publisher = TelegramPublisher(bot_token=publisher_token)

    async def post_init(application) -> None:
        scheduler = setup_scheduler(
            channels=list(runnable.values()),
            bot=application.bot,
            publisher=publisher,
            operator_user_id=settings.operator_telegram_user_id,
        )
        scheduler.start()
        application.bot_data["scheduler"] = scheduler
        log_scheduled_jobs(scheduler)
        logger.info("Scheduler started.")

    app = build_application(
        token=editor_token,
        publisher=publisher,
        channels=runnable,
        post_init=post_init,
    )

    logger.info("Active channels:")
    for cid, ch in runnable.items():
        logger.info(
            "  %s %s (%s) -> %s  [fetch=%s publish=%s tz=%s]",
            ch.emoji or "·",
            cid,
            ch.name,
            ch.telegram_channel_id,
            ch.cadence.fetch_time_local,
            ",".join(ch.cadence.publish_times_local),
            ch.cadence.timezone,
        )
    logger.info("Editor bot starting. /start in your Telegram DM to begin.")
    app.run_polling(stop_signals=None, bootstrap_retries=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
