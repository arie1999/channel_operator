"""Storage repository: thin DAL between pipeline / bot and SQLAlchemy."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update

from channels_operator.sources.base import RawItem
from channels_operator.storage.db import SessionLocal
from channels_operator.storage.models import CostLog, Decision, Draft, Item


@dataclass
class Stage1Result:
    passed: bool
    reason: str


# ─── items ──────────────────────────────────────────────────────────────


def save_items_idempotent(
    raws: list[RawItem],
    results: list[Stage1Result],
    channel_id: str,
) -> int:
    if len(raws) != len(results):
        raise ValueError("raws and results must have equal length")

    new_count = 0
    with SessionLocal() as session:
        for raw, res in zip(raws, results):
            existing = session.execute(
                select(Item.id)
                .where(Item.source == raw.source, Item.source_id == raw.source_id)
                .limit(1)
            ).first()
            if existing:
                continue
            session.add(
                Item(
                    channel_id=channel_id,
                    source=raw.source,
                    source_id=raw.source_id,
                    axis_hint=raw.axis_hint,
                    title=raw.title[:1024],
                    abstract=raw.abstract,
                    url=raw.url[:1024],
                    published_at=raw.published_at,
                    stage1_passed=res.passed,
                    stage1_reason=res.reason,
                )
            )
            new_count += 1
        session.commit()
    return new_count


def count_items(channel_id: str | None = None, only_stage1_passed: bool = False) -> int:
    with SessionLocal() as session:
        stmt = select(func.count()).select_from(Item)
        if channel_id is not None:
            stmt = stmt.where(Item.channel_id == channel_id)
        if only_stage1_passed:
            stmt = stmt.where(Item.stage1_passed.is_(True))
        return int(session.execute(stmt).scalar_one())


# ─── stage 2 ────────────────────────────────────────────────────────────


def get_stage2_pending(channel_id: str, limit: int | None = None) -> list[Item]:
    with SessionLocal() as session:
        stmt = (
            select(Item)
            .where(Item.channel_id == channel_id)
            .where(Item.stage1_passed.is_(True))
            .where(Item.stage2_passed.is_(None))
            .order_by(Item.published_at.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(session.execute(stmt).scalars().all())


def update_stage2_outcome(
    item_id: int,
    *,
    passed: bool,
    score: int,
    axis: str | None,
    topic_tag: str | None,
    audience_fit: str | None,
    rationale: str | None,
) -> None:
    with SessionLocal() as session:
        session.execute(
            update(Item)
            .where(Item.id == item_id)
            .values(
                stage2_passed=passed,
                stage2_score=score,
                stage2_axis=axis,
                stage2_topic_tag=topic_tag,
                stage2_audience_fit=audience_fit,
                stage2_rationale=rationale,
                stage2_at=datetime.now(timezone.utc),
            )
        )
        session.commit()


# ─── drafts ─────────────────────────────────────────────────────────────


def get_stage2_passed_without_draft(channel_id: str, limit: int | None = None) -> list[Item]:
    with SessionLocal() as session:
        existing_draft_item_ids = select(Draft.item_id)
        stmt = (
            select(Item)
            .where(Item.channel_id == channel_id)
            .where(Item.stage2_passed.is_(True))
            .where(Item.id.notin_(existing_draft_item_ids))
            .order_by(Item.published_at.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(session.execute(stmt).scalars().all())


def save_draft(
    *,
    item_id: int,
    channel_id: str,
    post_body: str,
    hashtags: list[str],
    headline_finding: str,
    methodological_caveat: str,
) -> int:
    with SessionLocal() as session:
        draft = Draft(
            item_id=item_id,
            channel_id=channel_id,
            post_body=post_body,
            hashtags=" ".join(hashtags),
            headline_finding=headline_finding,
            methodological_caveat=methodological_caveat,
        )
        session.add(draft)
        session.commit()
        return draft.id


def get_drafts_for_channel(channel_id: str, state: str | None = None) -> list[Draft]:
    with SessionLocal() as session:
        stmt = select(Draft).where(Draft.channel_id == channel_id)
        if state is not None:
            stmt = stmt.where(Draft.state == state)
        return list(session.execute(stmt.order_by(Draft.created_at.desc())).scalars().all())


def get_drafts_with_items(
    channel_id: str, state: str | None = None
) -> list[tuple[Draft, Item]]:
    with SessionLocal() as session:
        stmt = (
            select(Draft, Item)
            .join(Item, Draft.item_id == Item.id)
            .where(Draft.channel_id == channel_id)
        )
        if state is not None:
            stmt = stmt.where(Draft.state == state)
        rows = session.execute(stmt.order_by(Draft.created_at.desc())).all()
        return [(d, i) for d, i in rows]


def get_pending_drafts_with_items(
    channel_id: str, limit: int | None = None
) -> list[tuple[Draft, Item]]:
    """Pending drafts (oldest first) for batch DM to the operator."""
    with SessionLocal() as session:
        stmt = (
            select(Draft, Item)
            .join(Item, Draft.item_id == Item.id)
            .where(Draft.channel_id == channel_id)
            .where(Draft.state == "pending")
            .order_by(Draft.created_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = session.execute(stmt).all()
        return [(d, i) for d, i in rows]


def get_draft_with_item(draft_id: int) -> tuple[Draft, Item] | None:
    with SessionLocal() as session:
        row = session.execute(
            select(Draft, Item)
            .join(Item, Draft.item_id == Item.id)
            .where(Draft.id == draft_id)
        ).first()
        if row is None:
            return None
        d, i = row
        return d, i


def mark_draft_state(draft_id: int, state: str) -> None:
    with SessionLocal() as session:
        session.execute(
            update(Draft).where(Draft.id == draft_id).values(state=state)
        )
        session.commit()


def apply_edit(draft_id: int, new_body: str) -> None:
    """Replace the draft's post_body with operator-edited text. Marks
    state=edited; the operator must then approve or reject explicitly."""
    with SessionLocal() as session:
        session.execute(
            update(Draft)
            .where(Draft.id == draft_id)
            .values(post_body=new_body, state="edited")
        )
        session.commit()


def get_approved_pending_publish(channel_id: str) -> tuple[Draft, Item] | None:
    """Oldest approved draft awaiting publish, or None."""
    with SessionLocal() as session:
        row = session.execute(
            select(Draft, Item)
            .join(Item, Draft.item_id == Item.id)
            .where(Draft.channel_id == channel_id)
            .where(Draft.state == "approved")
            .order_by(Draft.created_at.asc())
            .limit(1)
        ).first()
        if row is None:
            return None
        d, i = row
        return d, i


def mark_published(draft_id: int, telegram_message_id: int) -> None:
    with SessionLocal() as session:
        session.execute(
            update(Draft)
            .where(Draft.id == draft_id)
            .values(
                state="published",
                published_message_id=telegram_message_id,
                published_at=datetime.now(timezone.utc),
            )
        )
        session.commit()


# ─── decisions ──────────────────────────────────────────────────────────


def log_decision(
    *,
    draft_id: int,
    channel_id: str,
    operator_telegram_user_id: int,
    action: str,
    reason: str | None = None,
    edit_text: str | None = None,
) -> None:
    with SessionLocal() as session:
        session.add(
            Decision(
                draft_id=draft_id,
                channel_id=channel_id,
                operator_telegram_user_id=operator_telegram_user_id,
                action=action,
                reason=reason,
                edit_text=edit_text,
            )
        )
        session.commit()


# ─── cost log ───────────────────────────────────────────────────────────


def log_cost(
    *,
    channel_id: str,
    item_id: int | None,
    stage: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    with SessionLocal() as session:
        session.add(
            CostLog(
                channel_id=channel_id,
                item_id=item_id,
                stage=stage,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )
        session.commit()


def cost_summary(channel_id: str) -> dict[str, dict]:
    with SessionLocal() as session:
        rows = session.execute(
            select(
                CostLog.stage,
                CostLog.model,
                func.count().label("calls"),
                func.sum(CostLog.prompt_tokens).label("prompt"),
                func.sum(CostLog.completion_tokens).label("completion"),
            )
            .where(CostLog.channel_id == channel_id)
            .group_by(CostLog.stage, CostLog.model)
        ).all()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r.stage, {})[r.model] = {
            "calls": int(r.calls or 0),
            "prompt_tokens": int(r.prompt or 0),
            "completion_tokens": int(r.completion or 0),
        }
    return out
