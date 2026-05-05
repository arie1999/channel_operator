"""Source health monitoring.

Heuristic: a source is "healthy" if it produced at least one Item in
the last `stale_after_days` (default 7). Configured sources that have
produced 0 ever — or 0 recently — are flagged. The operator's daily
summary surfaces the flagged list.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import func, select

from channels_operator.storage.db import SessionLocal
from channels_operator.storage.models import Item


def source_health(
    channel_id: str,
    channels_root: Path,
    stale_after_days: int = 7,
) -> dict[str, dict]:
    """For each source declared in the channel's sources.yaml, compute
    a status: 'ok' | 'stale' | 'never_returned'."""
    channel_dir = channels_root / channel_id
    sources_yaml = channel_dir / "sources.yaml"
    if not sources_yaml.exists():
        return {}
    declared = yaml.safe_load(sources_yaml.read_text(encoding="utf-8"))
    names = [entry["name"] for entry in declared.get("sources", [])]

    with SessionLocal() as session:
        rows = session.execute(
            select(
                Item.source,
                func.max(Item.fetched_at).label("last_fetched"),
                func.count().label("count_total"),
            )
            .where(Item.channel_id == channel_id)
            .group_by(Item.source)
        ).all()
    last_seen: dict[str, tuple[datetime | None, int]] = {
        r.source: (r.last_fetched, int(r.count_total or 0)) for r in rows
    }

    now = datetime.now(timezone.utc)
    threshold = now - timedelta(days=stale_after_days)

    results: dict[str, dict] = {}
    for name in names:
        seen = last_seen.get(name)
        if seen is None:
            results[name] = {"status": "never_returned", "last_fetched_at": None, "count_total": 0}
            continue
        last_at, count = seen
        if last_at is not None and last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        if last_at is None or last_at < threshold:
            results[name] = {"status": "stale", "last_fetched_at": last_at, "count_total": count}
        else:
            results[name] = {"status": "ok", "last_fetched_at": last_at, "count_total": count}
    return results
