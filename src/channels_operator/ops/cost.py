"""Per-call cost computation.

Token counts come from CostLog rows (logged at every LLM call).
USD is computed at query time from the rate table below — that way
pricing changes don't require schema migrations or DB rewrites.

Update MODEL_RATES as OpenRouter pricing changes. Models missing from
the table are reported as $0 cost (not an error — just unknown).
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from channels_operator.storage.db import SessionLocal
from channels_operator.storage.models import CostLog


# (input_per_million_usd, output_per_million_usd) — checked 2026-05-05
MODEL_RATES: dict[str, tuple[float, float]] = {
    "anthropic/claude-haiku-4-5":  (0.80, 4.00),
    "anthropic/claude-sonnet-4-6": (3.00, 15.00),
    "anthropic/claude-opus-4-7":   (15.00, 75.00),
    "qwen/qwen3.6-plus":           (0.33, 1.95),
    "qwen/qwen3.6-flash":          (0.25, 1.50),
    "openai/gpt-5":                (1.25, 10.00),
    "openai/gpt-5-chat":           (1.25, 10.00),
    "openai/gpt-5-mini":           (0.25, 2.00),
    "openai/gpt-5-nano":           (0.05, 0.40),
    "google/gemini-2.5-pro":       (1.25, 10.00),
    "google/gemini-2.5-flash":     (0.30, 2.50),
    "google/gemini-2.5-flash-lite": (0.10, 0.40),
}


def compute_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = MODEL_RATES.get(model)
    if rates is None:
        return 0.0
    in_rate, out_rate = rates
    return (prompt_tokens / 1_000_000) * in_rate + (completion_tokens / 1_000_000) * out_rate


def cost_for_window(channel_id: str, since: datetime) -> dict[str, dict]:
    """{stage: {"calls": int, "prompt": int, "completion": int, "usd": float}}."""
    out: dict[str, dict] = {}
    with SessionLocal() as session:
        rows = session.execute(
            select(
                CostLog.stage,
                CostLog.model,
                CostLog.prompt_tokens,
                CostLog.completion_tokens,
            )
            .where(CostLog.channel_id == channel_id)
            .where(CostLog.timestamp >= since)
        ).all()
    for stage, model, p, c in rows:
        bucket = out.setdefault(
            stage, {"calls": 0, "prompt": 0, "completion": 0, "usd": 0.0}
        )
        bucket["calls"] += 1
        bucket["prompt"] += int(p or 0)
        bucket["completion"] += int(c or 0)
        bucket["usd"] += compute_cost_usd(model, int(p or 0), int(c or 0))
    return out


def cost_today_and_mtd(channel_id: str) -> tuple[float, float]:
    """USD costs for today (UTC day) and month-to-date."""
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    today = sum(s["usd"] for s in cost_for_window(channel_id, day_start).values())
    mtd = sum(s["usd"] for s in cost_for_window(channel_id, month_start).values())
    return today, mtd
