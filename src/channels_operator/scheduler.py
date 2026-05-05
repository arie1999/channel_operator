"""APScheduler integration: per-channel jobs + global ops jobs.

One scheduler instance runs alongside the editor bot's async runtime.
Per-channel jobs:
  fetch_<channel>          at cadence.fetch_time_local — runs Stage 1 -> 3.
  batch_dm_<channel>       1 hour after fetch — DMs operator the queue.
  publish_<channel>_<slot> at each cadence.publish_times_local — picks
                            one approved draft and publishes it.
  summary_<channel>        21:00 channel-local — DMs the daily summary.

Global jobs (registered once):
  backup                   03:00 channel-0-local — snapshots SQLite.
"""

import asyncio
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot

from channels_operator.config.models import ChannelConfig
from channels_operator.ops.backup import backup_db
from channels_operator.ops.summary import build_daily_summary
from channels_operator.pipeline.orchestrator import run_pipeline
from channels_operator.publishing.telegram import TelegramPublisher
from channels_operator.storage.repository import (
    get_approved_pending_publish,
    mark_published,
)
from channels_operator.telegram.editor_bot import send_pending_batch

logger = logging.getLogger(__name__)


CHANNELS_ROOT = Path("channels")


def _parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


# ─── job functions ──────────────────────────────────────────────────────


async def fetch_job(channel_id: str) -> None:
    logger.info("Scheduled fetch starting: channel=%s", channel_id)
    try:
        result = await asyncio.to_thread(run_pipeline, channel_id, CHANNELS_ROOT)
    except Exception:
        logger.exception("Scheduled fetch failed: channel=%s", channel_id)
        return
    logger.info(
        "Scheduled fetch done: channel=%s stage2_pass=%d stage3_drafts=%d",
        channel_id,
        result["stage2"]["passed"],
        result["stage3"]["summarized"],
    )


async def batch_dm_job(
    channel: ChannelConfig,
    bot: Bot,
    operator_user_id: int,
    max_drafts: int,
) -> None:
    try:
        sent = await send_pending_batch(
            bot=bot,
            operator_user_id=operator_user_id,
            channel=channel,
            max_count=max_drafts,
        )
    except Exception:
        logger.exception("Scheduled batch DM failed: channel=%s", channel.id)
        return
    logger.info("Scheduled batch DM: channel=%s sent=%d", channel.id, sent)


async def publish_slot_job(
    channel: ChannelConfig,
    publisher: TelegramPublisher,
) -> None:
    pair = get_approved_pending_publish(channel.id)
    if pair is None:
        logger.info("Publish slot: no approved drafts for %s", channel.id)
        return
    draft, item = pair
    body = f"{draft.post_body}\n\n{draft.hashtags}\n\n📎 {item.url}"
    try:
        msg_id = await publisher.publish(
            channel_id=channel.telegram_channel_id, text=body
        )
    except Exception:
        logger.exception(
            "Publish slot: publish failed for draft=%d channel=%s",
            draft.id, channel.id,
        )
        return
    mark_published(draft.id, telegram_message_id=msg_id)
    logger.info(
        "Publish slot: published draft=%d channel=%s msg_id=%d",
        draft.id, channel.telegram_channel_id, msg_id,
    )


async def daily_summary_job(
    channel: ChannelConfig,
    bot: Bot,
    operator_user_id: int,
) -> None:
    try:
        text = build_daily_summary(channel, CHANNELS_ROOT)
        await bot.send_message(
            chat_id=operator_user_id,
            text=text,
            disable_web_page_preview=True,
        )
        logger.info("Daily summary sent: channel=%s", channel.id)
    except Exception:
        logger.exception("Daily summary failed: channel=%s", channel.id)


async def backup_job() -> None:
    try:
        await asyncio.to_thread(backup_db)
    except Exception:
        logger.exception("DB backup failed")


# ─── setup ──────────────────────────────────────────────────────────────


def _register_channel_jobs(
    scheduler: AsyncIOScheduler,
    channel: ChannelConfig,
    bot: Bot,
    publisher: TelegramPublisher,
    operator_user_id: int,
) -> None:
    tz = ZoneInfo(channel.cadence.timezone)
    fetch_h, fetch_m = _parse_hhmm(channel.cadence.fetch_time_local)

    scheduler.add_job(
        fetch_job,
        CronTrigger(hour=fetch_h, minute=fetch_m, timezone=tz),
        kwargs={"channel_id": channel.id},
        id=f"fetch_{channel.id}",
        replace_existing=True,
    )

    dm_h = (fetch_h + 1) % 24
    scheduler.add_job(
        batch_dm_job,
        CronTrigger(hour=dm_h, minute=fetch_m, timezone=tz),
        kwargs={
            "channel": channel,
            "bot": bot,
            "operator_user_id": operator_user_id,
            "max_drafts": channel.cadence.drafts_per_day_max,
        },
        id=f"batch_dm_{channel.id}",
        replace_existing=True,
    )

    for slot in channel.cadence.publish_times_local:
        h, m = _parse_hhmm(slot)
        scheduler.add_job(
            publish_slot_job,
            CronTrigger(hour=h, minute=m, timezone=tz),
            kwargs={"channel": channel, "publisher": publisher},
            id=f"publish_{channel.id}_{slot}",
            replace_existing=True,
        )

    scheduler.add_job(
        daily_summary_job,
        CronTrigger(hour=21, minute=0, timezone=tz),
        kwargs={
            "channel": channel,
            "bot": bot,
            "operator_user_id": operator_user_id,
        },
        id=f"summary_{channel.id}",
        replace_existing=True,
    )


def setup_scheduler(
    channels: list[ChannelConfig],
    bot: Bot,
    publisher: TelegramPublisher,
    operator_user_id: int,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    for channel in channels:
        _register_channel_jobs(scheduler, channel, bot, publisher, operator_user_id)

    # Single global backup job. Use the first channel's timezone for the
    # 03:00 anchor — backups happen once per day regardless of channel count.
    if channels:
        tz = ZoneInfo(channels[0].cadence.timezone)
        scheduler.add_job(
            backup_job,
            CronTrigger(hour=3, minute=0, timezone=tz),
            id="backup",
            replace_existing=True,
        )

    return scheduler


def log_scheduled_jobs(scheduler: AsyncIOScheduler) -> None:
    for job in sorted(scheduler.get_jobs(), key=lambda j: (j.next_run_time or 0, j.id)):
        logger.info("scheduled: %s -> next_run=%s", job.id, job.next_run_time)
