"""Stage 2: cheap LLM relevance scoring.

For each Stage-1-passed item, the model returns a 0-10 relevance score,
an axis classification, and an audience-fit judgment. Pass criteria:
score >= 7 AND audience_fit == 'professional'.
"""

import logging
from dataclasses import dataclass

from channels_operator.config.models import ChannelConfig
from channels_operator.llm.base import LLMClient
from channels_operator.storage.models import Item

logger = logging.getLogger(__name__)


PASS_THRESHOLD = 7


@dataclass
class Stage2Decision:
    item_id: int
    passed: bool
    score: int
    axis: str | None
    topic_tag: str | None
    audience_fit: str | None
    rationale: str | None
    usage: dict


def _format_user_message(item: Item) -> str:
    return (
        "ITEM TO SCORE\n"
        f"Title: {item.title}\n"
        f"Source: {item.source}\n"
        f"Axis hint: {item.axis_hint or '(none)'}\n"
        "Abstract:\n"
        f"{item.abstract}"
    )


def stage2_score(
    item: Item,
    channel: ChannelConfig,
    client: LLMClient,
    relevance_prompt: str,
) -> Stage2Decision:
    parsed, usage = client.score(
        system_prompt=relevance_prompt,
        user_prompt=_format_user_message(item),
        model=channel.models.stage2.model,
    )
    try:
        score = int(parsed.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    audience_fit = parsed.get("audience_fit") or "lay"
    passed = score >= PASS_THRESHOLD and audience_fit == "professional"
    return Stage2Decision(
        item_id=item.id,
        passed=passed,
        score=score,
        axis=parsed.get("axis"),
        topic_tag=parsed.get("topic_tag"),
        audience_fit=audience_fit,
        rationale=parsed.get("rationale"),
        usage=usage,
    )
