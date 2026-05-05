"""Daily summary message: drafts pending, posts published, cost, source health.

Drives both the scheduled 21:00 DM and the operator's `/status` command.
Same builder, same output.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from channels_operator.config.models import ChannelConfig
from channels_operator.ops.cost import cost_for_window, cost_today_and_mtd
from channels_operator.ops.health import source_health
from channels_operator.storage.db import SessionLocal
from channels_operator.storage.models import Decision, Draft, Item


def build_daily_summary(channel: ChannelConfig, channels_root: Path) -> str:
    now = datetime.now(timezone.utc)
    day_start = now - timedelta(hours=24)
    week_start = now - timedelta(days=7)

    with SessionLocal() as session:
        items_24h = session.execute(
            select(func.count()).select_from(Item)
            .where(Item.channel_id == channel.id)
            .where(Item.fetched_at >= day_start)
        ).scalar_one()

        stage1_24h = session.execute(
            select(func.count()).select_from(Item)
            .where(Item.channel_id == channel.id)
            .where(Item.fetched_at >= day_start)
            .where(Item.stage1_passed.is_(True))
        ).scalar_one()

        stage2_passed_24h = session.execute(
            select(func.count()).select_from(Item)
            .where(Item.channel_id == channel.id)
            .where(Item.stage2_at >= day_start)
            .where(Item.stage2_passed.is_(True))
        ).scalar_one()

        drafts_24h = session.execute(
            select(func.count()).select_from(Draft)
            .where(Draft.channel_id == channel.id)
            .where(Draft.created_at >= day_start)
        ).scalar_one()

        decisions = session.execute(
            select(Decision.action, func.count())
            .where(Decision.channel_id == channel.id)
            .where(Decision.timestamp >= day_start)
            .group_by(Decision.action)
        ).all()
        action_counts = {a: int(c) for a, c in decisions}

        pending = session.execute(
            select(func.count()).select_from(Draft)
            .where(Draft.channel_id == channel.id)
            .where(Draft.state == "pending")
        ).scalar_one()
        approved = session.execute(
            select(func.count()).select_from(Draft)
            .where(Draft.channel_id == channel.id)
            .where(Draft.state == "approved")
        ).scalar_one()
        published_24h = session.execute(
            select(func.count()).select_from(Draft)
            .where(Draft.channel_id == channel.id)
            .where(Draft.published_at >= day_start)
        ).scalar_one()
        published_7d = session.execute(
            select(func.count()).select_from(Draft)
            .where(Draft.channel_id == channel.id)
            .where(Draft.published_at >= week_start)
        ).scalar_one()

        axis_rows = session.execute(
            select(Item.stage2_axis, func.count())
            .join(Draft, Draft.item_id == Item.id)
            .where(Draft.channel_id == channel.id)
            .where(Draft.published_at >= week_start)
            .group_by(Item.stage2_axis)
        ).all()
        axis_counts = {(a or "?"): int(c) for a, c in axis_rows}

    today_cost, mtd_cost = cost_today_and_mtd(channel.id)
    cost_breakdown = cost_for_window(channel.id, day_start)

    health = source_health(channel.id, channels_root)
    healthy = sum(1 for h in health.values() if h["status"] == "ok")
    stale = [n for n, h in health.items() if h["status"] in ("stale", "never_returned")]

    lines: list[str] = []
    lines.append(f"📊 {channel.name} — {now.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append("Pipeline (24h):")
    lines.append(f"  fetched:      {items_24h}")
    lines.append(f"  stage 1 pass: {stage1_24h}")
    lines.append(f"  stage 2 pass: {stage2_passed_24h}")
    lines.append(f"  stage 3 drafts: {drafts_24h}")
    lines.append("")
    lines.append("Operator (24h):")
    if action_counts:
        for action in ("approve", "edit", "reject", "skip"):
            if action in action_counts:
                lines.append(f"  {action}: {action_counts[action]}")
    else:
        lines.append("  (no decisions)")
    lines.append("")
    lines.append("Queue:")
    lines.append(f"  pending:          {pending}")
    lines.append(f"  awaiting publish: {approved}")
    lines.append(f"  published (24h):  {published_24h}")
    lines.append(f"  published (7d):   {published_7d}")
    if axis_counts:
        mix_parts = []
        for axis_id, label in [("A", "clinical"), ("B", "AI")]:
            if axis_id in axis_counts:
                mix_parts.append(f"{axis_id}={axis_counts[axis_id]}")
        if mix_parts:
            lines.append(f"  axis mix (7d):    {' '.join(mix_parts)}")
    lines.append("")
    lines.append("Cost:")
    if cost_breakdown:
        for stage in ("stage2", "stage3"):
            if stage in cost_breakdown:
                b = cost_breakdown[stage]
                lines.append(f"  {stage}: ${b['usd']:.4f} ({b['calls']} calls)")
    else:
        lines.append("  (no LLM calls in 24h)")
    lines.append(f"  today:    ${today_cost:.4f}")
    lines.append(f"  month-to-date: ${mtd_cost:.4f}")
    lines.append("")
    lines.append("Source health:")
    if health:
        lines.append(f"  ✅ {healthy}/{len(health)} working")
        if stale:
            preview = ", ".join(stale[:5])
            extra = f" +{len(stale) - 5} more" if len(stale) > 5 else ""
            lines.append(f"  ⚠️ silent ≥7d: {preview}{extra}")
    else:
        lines.append("  (no source data)")

    return "\n".join(lines)
