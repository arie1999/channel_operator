"""SQLAlchemy models for the operator's data.

Schema is intentionally Postgres-portable — no SQLite-only types or
syntax. Same DDL runs on Postgres later for SaaS migration.

Tables:
  Item       Raw items from sources, deduped on (source, source_id).
             Stage 1 + Stage 2 outcomes are columns on this table.
  Draft      Stage 3 output ready for operator review. State machine:
             pending -> approved | rejected | edited | skipped, then
             approved -> published.
  Decision   Operator action log: every approve / edit / reject / skip
             click is one row.
  CostLog    Per-LLM-call accounting. SaaS-billing-ready (per-call grain).
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(512))
    axis_hint: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    title: Mapped[str] = mapped_column(String(1024))
    abstract: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1024))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)

    # Stage 1
    stage1_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    stage1_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    # Stage 2 — null until scored
    stage2_passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    stage2_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stage2_axis: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    stage2_topic_tag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    stage2_audience_fit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    stage2_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stage2_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_items_source_source_id"),
        Index("ix_items_channel_published", "channel_id", "published_at"),
    )

    def __repr__(self) -> str:
        return f"Item(id={self.id}, source={self.source!r}, source_id={self.source_id!r})"


class Draft(Base):
    """A Stage 3 output ready for operator review."""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("items.id"), unique=True)
    channel_id: Mapped[str] = mapped_column(String(64), index=True)

    post_body: Mapped[str] = mapped_column(Text)
    hashtags: Mapped[str] = mapped_column(String(256))
    headline_finding: Mapped[str] = mapped_column(Text)
    methodological_caveat: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc)
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)

    # Set when state transitions to "published"
    published_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"Draft(id={self.id}, item_id={self.item_id}, state={self.state!r})"


class Decision(Base):
    """Operator action on a Draft. Multiple Decisions per Draft are
    expected (e.g., edit then approve)."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc, index=True)
    draft_id: Mapped[int] = mapped_column(Integer, ForeignKey("drafts.id"), index=True)
    channel_id: Mapped[str] = mapped_column(String(64), index=True)
    operator_telegram_user_id: Mapped[int] = mapped_column(BigInteger)

    # action: approve | reject | edit | skip
    action: Mapped[str] = mapped_column(String(32))
    # reason: for reject only — not_relevant | low_quality | duplicate | bad_translation | other
    reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # edit_text: the operator-submitted Hebrew text (for edit actions only)
    edit_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class CostLog(Base):
    __tablename__ = "cost_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now_utc, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), index=True)
    item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stage: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
