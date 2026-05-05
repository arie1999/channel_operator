"""Editor bot: DMs Drafts to the operator with five action buttons.

Phase 6: multi-channel. The bot serves all configured channels through
one operator inbox. Each draft DM shows the channel name and emoji in
the header; Approve looks up the draft's channel_id to find the right
publish destination.

Commands:
  /start         confirms the bot, shows per-channel pending counts
  /draft         sends drafts from each channel up to its drafts_per_day_max
  /status        per-channel daily summary, concatenated
  /publish_now   admin: publish the oldest approved draft from any channel
  /cancel        abort an in-progress edit
"""

import logging
from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from channels_operator.config.models import ChannelConfig
from channels_operator.ops.summary import build_daily_summary
from channels_operator.publishing.telegram import TelegramPublisher
from channels_operator.settings import settings
from channels_operator.storage.models import Draft, Item
from channels_operator.storage.repository import (
    apply_edit,
    get_approved_pending_publish,
    get_draft_with_item,
    get_drafts_for_channel,
    get_pending_drafts_with_items,
    log_decision,
    mark_draft_state,
    mark_published,
)


CHANNELS_ROOT = Path("channels")

logger = logging.getLogger(__name__)


REJECT_REASONS: list[tuple[str, str]] = [
    ("Not relevant", "not_relevant"),
    ("Low quality", "low_quality"),
    ("Duplicate", "duplicate"),
    ("Bad translation", "bad_translation"),
    ("Other", "other"),
]


# ─── helpers ────────────────────────────────────────────────────────────


def _channels(context: ContextTypes.DEFAULT_TYPE) -> dict[str, ChannelConfig]:
    return context.application.bot_data["channels"]


def _channel_for_draft(
    draft: Draft, context: ContextTypes.DEFAULT_TYPE
) -> ChannelConfig | None:
    channels = _channels(context)
    return channels.get(draft.channel_id)


def _channel_index_map(channels: dict[str, ChannelConfig]) -> dict[int, str]:
    """Stable 1-based indices for channels, ordered by channel id (alpha)."""
    return {i + 1: cid for i, cid in enumerate(sorted(channels.keys()))}


def _resolve_channels(
    args: list[str],
    all_channels: dict[str, ChannelConfig],
) -> tuple[dict[str, ChannelConfig], str | None]:
    """Resolve a command's optional channel argument.

    No arg          -> all channels.
    "<index>"       -> the indexed channel (1-based, alpha-sorted).
    "<channel_id>"  -> the channel with that id.
    Unknown -> empty dict + error message.
    """
    if not args:
        return all_channels, None
    requested = args[0].strip().lstrip("@")

    # Numeric index
    if requested.isdigit():
        idx_map = _channel_index_map(all_channels)
        cid = idx_map.get(int(requested))
        if cid is not None:
            return {cid: all_channels[cid]}, None
        valid = ", ".join(f"{i}={c}" for i, c in idx_map.items())
        return {}, f"Unknown channel index '{requested}'. Valid: {valid}"

    # Channel id
    if requested in all_channels:
        return {requested: all_channels[requested]}, None
    valid = ", ".join(sorted(all_channels.keys()))
    return {}, f"Unknown channel '{requested}'. Valid: {valid}"


# ─── message formatting ─────────────────────────────────────────────────


def _format_publish_body(draft: Draft, item: Item) -> str:
    """The exact text that will be posted to the channel on Approve."""
    return f"{draft.post_body}\n\n{draft.hashtags}\n\n📎 {item.url}"


def _format_review_message(draft: Draft, item: Item, channel: ChannelConfig) -> str:
    """The DM text the operator sees in their review window."""
    score = item.stage2_score if item.stage2_score is not None else "?"
    axis = item.stage2_axis or "?"
    topic = item.stage2_topic_tag or "untagged"
    emoji = (channel.emoji + " ") if channel.emoji else ""
    header = f"{emoji}{channel.name} · Axis {axis} · score {score} · {topic}"
    caveat = draft.methodological_caveat or "(no caveat)"
    return (
        f"{header}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"{_format_publish_body(draft, item)}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"Source: `{item.source}`\n"
        f"⚠️ {caveat}"
    )


def _draft_keyboard(draft_id: int, source_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{draft_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{draft_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject:{draft_id}"),
            ],
            [
                InlineKeyboardButton(
                    "🔗 Open Source", url=source_url or "https://example.com"
                ),
                InlineKeyboardButton("⏭️ Skip", callback_data=f"skip:{draft_id}"),
            ],
        ]
    )


def _reject_reasons_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"reject_reason:{draft_id}:{code}")]
        for label, code in REJECT_REASONS
    ]
    rows.append(
        [InlineKeyboardButton("⬅ Back", callback_data=f"reject_back:{draft_id}")]
    )
    return InlineKeyboardMarkup(rows)


# ─── send-to-operator helpers ───────────────────────────────────────────


async def send_draft_to_operator(
    bot: Bot,
    operator_user_id: int,
    draft: Draft,
    item: Item,
    channel: ChannelConfig,
) -> None:
    await bot.send_message(
        chat_id=operator_user_id,
        text=_format_review_message(draft, item, channel),
        reply_markup=_draft_keyboard(draft.id, item.url),
        disable_web_page_preview=True,
    )


async def send_pending_batch(
    bot: Bot,
    operator_user_id: int,
    channel: ChannelConfig,
    max_count: int,
) -> int:
    """Send up to `max_count` pending drafts for ONE channel. Returns
    the count actually sent."""
    pairs = get_pending_drafts_with_items(channel.id, limit=max_count)
    for draft, item in pairs:
        await send_draft_to_operator(bot, operator_user_id, draft, item, channel)
    return len(pairs)


async def send_pending_batch_multi(
    bot: Bot,
    operator_user_id: int,
    channels: dict[str, ChannelConfig],
) -> dict[str, int]:
    """Send each channel's pending drafts up to its drafts_per_day_max.
    Returns {channel_id: count_sent}."""
    counts: dict[str, int] = {}
    for cid, ch in channels.items():
        sent = await send_pending_batch(
            bot=bot,
            operator_user_id=operator_user_id,
            channel=ch,
            max_count=ch.cadence.drafts_per_day_max,
        )
        counts[cid] = sent
    return counts


# ─── auth ───────────────────────────────────────────────────────────────


def _is_authorized(update: Update) -> bool:
    expected = settings.operator_telegram_user_id
    if not expected:
        return True
    if update.effective_user is None:
        return False
    return update.effective_user.id == expected


# ─── commands ───────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return
    if not _is_authorized(update):
        await update.effective_chat.send_message(
            f"This editor bot is configured for a single operator. "
            f"Your Telegram user ID is {update.effective_user.id}; "
            f"the configured operator is someone else."
        )
        return
    channels = _channels(context)
    idx_map = _channel_index_map(channels)
    lines = ["Channels Operator editor bot ready.", ""]
    lines.append(f"Active channels ({len(channels)}):")
    for idx, cid in idx_map.items():
        ch = channels[cid]
        emoji = (ch.emoji + " ") if ch.emoji else ""
        pending = len(get_drafts_for_channel(ch.id, state="pending"))
        approved = len(get_drafts_for_channel(ch.id, state="approved"))
        lines.append(
            f"  [{idx}] {emoji}{cid} ({ch.name}) -> {ch.telegram_channel_id}"
            f"  · pending={pending} approved={approved}"
        )
    lines.append("")
    lines.append("Commands (each accepts an optional index or channel id):")
    lines.append("/draft [N|id]       — send pending drafts")
    lines.append("/status [N|id]      — pipeline + queue + cost summary")
    lines.append("/publish_now [N|id] — publish the oldest approved draft")
    lines.append("/help               — this message")
    lines.append("")
    lines.append("Examples:")
    if idx_map:
        first_idx = min(idx_map.keys())
        first_cid = idx_map[first_idx]
        lines.append(f"  /draft {first_idx}              (= {first_cid})")
        lines.append(f"  /draft {first_cid}    (same, by id)")
    lines.append("  /draft               (all channels)")
    await update.effective_chat.send_message("\n".join(lines))


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def cmd_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return
    if not _is_authorized(update):
        return
    all_channels = _channels(context)
    filtered, err = _resolve_channels(context.args or [], all_channels)
    if err:
        await update.effective_chat.send_message(err)
        return
    counts = await send_pending_batch_multi(
        bot=context.bot,
        operator_user_id=update.effective_user.id,
        channels=filtered,
    )
    total = sum(counts.values())
    if total == 0:
        scope = "all channels" if len(filtered) > 1 else next(iter(filtered.keys()), "?")
        await update.effective_chat.send_message(
            f"No pending drafts on {scope}. Run a fetch (or wait) to populate."
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or not _is_authorized(update):
        return
    all_channels = _channels(context)
    filtered, err = _resolve_channels(context.args or [], all_channels)
    if err:
        await update.effective_chat.send_message(err)
        return
    # Reverse-lookup so each summary can show its [index]
    idx_by_cid = {cid: idx for idx, cid in _channel_index_map(all_channels).items()}
    parts: list[str] = []
    for ch in filtered.values():
        prefix = f"[{idx_by_cid.get(ch.id, '?')}] "
        parts.append(prefix + build_daily_summary(ch, CHANNELS_ROOT))
    text = "\n\n━━━━━━━━━━━━━━━\n\n".join(parts)
    await update.effective_chat.send_message(text=text, disable_web_page_preview=True)


async def cmd_publish_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or not _is_authorized(update):
        return
    all_channels = _channels(context)
    filtered, err = _resolve_channels(context.args or [], all_channels)
    if err:
        await update.effective_chat.send_message(err)
        return
    publisher: TelegramPublisher = context.application.bot_data["publisher"]
    # Search across the filtered set; publish the oldest approved.
    best: tuple[ChannelConfig, Draft, Item] | None = None
    for ch in filtered.values():
        pair = get_approved_pending_publish(ch.id)
        if pair is None:
            continue
        d, i = pair
        if best is None or d.created_at < best[1].created_at:
            best = (ch, d, i)
    if best is None:
        scope = "any channel" if len(filtered) > 1 else next(iter(filtered.keys()), "?")
        await update.effective_chat.send_message(
            f"No approved drafts awaiting publish on {scope}."
        )
        return
    channel, draft, item = best
    try:
        msg_id = await publisher.publish(
            channel_id=channel.telegram_channel_id,
            text=_format_publish_body(draft, item),
        )
    except Exception as exc:
        logger.exception("Manual publish failed for draft %d", draft.id)
        await update.effective_chat.send_message(
            f"❌ Publish FAILED: {type(exc).__name__}: {exc}"
        )
        return
    mark_published(draft.id, telegram_message_id=msg_id)
    await update.effective_chat.send_message(
        f"✅ Published draft {draft.id} ({channel.name}) to "
        f"{channel.telegram_channel_id} (msg_id={msg_id})."
    )


# ─── button callbacks ───────────────────────────────────────────────────


def _parse_draft_id(data: str) -> int:
    return int(data.split(":")[1])


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()

    if not _is_authorized(update):
        await query.edit_message_text("Not authorized.")
        return

    data = query.data
    action = data.split(":", 1)[0]
    operator_id = update.effective_user.id

    if action == "approve":
        draft_id = _parse_draft_id(data)
        pair = get_draft_with_item(draft_id)
        if pair is None:
            await query.edit_message_text("Draft no longer available.")
            return
        draft, _ = pair
        mark_draft_state(draft_id, "approved")
        log_decision(
            draft_id=draft_id,
            channel_id=draft.channel_id,
            operator_telegram_user_id=operator_id,
            action="approve",
        )
        channel = _channel_for_draft(draft, context)
        target = channel.telegram_channel_id if channel else "(unknown)"
        await query.edit_message_text(
            f"✅ Approved → will publish to {target} at the next configured slot.\n"
            f"(Or use /publish_now to fire immediately for testing.)"
        )
        return

    if action == "skip":
        draft_id = _parse_draft_id(data)
        pair = get_draft_with_item(draft_id)
        channel_id = pair[0].channel_id if pair else "?"
        log_decision(
            draft_id=draft_id,
            channel_id=channel_id,
            operator_telegram_user_id=operator_id,
            action="skip",
        )
        await query.edit_message_text("⏭️ Skipped — will reappear in the next batch.")
        return

    if action == "reject":
        draft_id = _parse_draft_id(data)
        await query.edit_message_reply_markup(
            reply_markup=_reject_reasons_keyboard(draft_id)
        )
        return

    if action == "reject_back":
        draft_id = _parse_draft_id(data)
        pair = get_draft_with_item(draft_id)
        if pair is None:
            await query.edit_message_text("Draft no longer available.")
            return
        _, item = pair
        await query.edit_message_reply_markup(
            reply_markup=_draft_keyboard(draft_id, item.url)
        )
        return

    if action == "reject_reason":
        parts = data.split(":")
        draft_id = int(parts[1])
        reason = parts[2]
        pair = get_draft_with_item(draft_id)
        channel_id = pair[0].channel_id if pair else "?"
        mark_draft_state(draft_id, "rejected")
        log_decision(
            draft_id=draft_id,
            channel_id=channel_id,
            operator_telegram_user_id=operator_id,
            action="reject",
            reason=reason,
        )
        await query.edit_message_text(f"❌ Rejected ({reason}).")
        return

    if action == "edit":
        draft_id = _parse_draft_id(data)
        if context.user_data is not None:
            context.user_data["editing_draft_id"] = draft_id
        await query.edit_message_text(
            "✏️ Send the edited text as a single message. /cancel to abort."
        )
        return

    await query.edit_message_text(f"Unknown action: {action}")


# ─── edit-text MessageHandler ───────────────────────────────────────────


async def on_text_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None or update.effective_chat is None:
        return
    if not _is_authorized(update):
        return

    draft_id = (context.user_data or {}).get("editing_draft_id")
    if draft_id is None:
        return  # not in edit mode

    new_text = (update.message.text or "").strip()
    if not new_text:
        await update.effective_chat.send_message("Empty edit. Send the new text or /cancel.")
        return

    pair = get_draft_with_item(draft_id)
    if pair is None:
        await update.effective_chat.send_message("Draft disappeared.")
        if context.user_data is not None:
            context.user_data.pop("editing_draft_id", None)
        return
    draft, item = pair
    apply_edit(draft_id, new_body=new_text)
    log_decision(
        draft_id=draft_id,
        channel_id=draft.channel_id,
        operator_telegram_user_id=update.effective_user.id,
        action="edit",
        edit_text=new_text,
    )
    if context.user_data is not None:
        context.user_data.pop("editing_draft_id", None)

    # Re-fetch to get the updated body
    pair = get_draft_with_item(draft_id)
    if pair is None:
        return
    draft, item = pair
    channel = _channel_for_draft(draft, context)
    if channel is None:
        await update.effective_chat.send_message(
            f"Edit applied to draft {draft_id} but its channel ({draft.channel_id}) is "
            f"not currently active."
        )
        return
    await update.effective_chat.send_message(
        text="✏️ Edit applied. Re-review:\n\n"
        + _format_review_message(draft, item, channel),
        reply_markup=_draft_keyboard(draft.id, item.url),
        disable_web_page_preview=True,
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or not _is_authorized(update):
        return
    if context.user_data is not None:
        had = context.user_data.pop("editing_draft_id", None)
    else:
        had = None
    if had:
        await update.effective_chat.send_message("Edit cancelled.")
    else:
        await update.effective_chat.send_message("Nothing to cancel.")


# ─── builder ────────────────────────────────────────────────────────────


def build_application(
    *,
    token: str,
    publisher: TelegramPublisher,
    channels: dict[str, ChannelConfig],
    post_init=None,
) -> Application:
    builder = (
        Application.builder()
        .token(token)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .get_updates_connect_timeout(30.0)
        .get_updates_read_timeout(60.0)
    )
    if post_init is not None:
        builder = builder.post_init(post_init)
    app = builder.build()
    app.bot_data["publisher"] = publisher
    app.bot_data["channels"] = channels
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("draft", cmd_draft))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("publish_now", cmd_publish_now))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_for_edit))
    return app
