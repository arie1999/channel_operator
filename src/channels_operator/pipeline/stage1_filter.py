"""Stage 1: keyword include/exclude, recency, abstract-presence. No AI calls.

Dedup against the DB happens in the repository, not here — Stage 1 is
about whether an item is editorially eligible, not whether it is new.
"""

import re
from datetime import datetime, timedelta, timezone

from channels_operator.config.models import KeywordsConfig
from channels_operator.sources.base import RawItem
from channels_operator.storage.repository import Stage1Result


DEFAULT_MAX_AGE_DAYS = 14


def _matches(term: str, text: str) -> bool:
    """Whole-token, case-insensitive match. Multi-word terms match the
    literal phrase with word boundaries on each end."""
    pattern = r"\b" + re.escape(term.strip().lower()) + r"\b"
    return bool(re.search(pattern, text))


def stage1_filter(
    item: RawItem,
    keywords: KeywordsConfig,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> Stage1Result:
    """Return whether an item passes Stage 1, plus the reason.

    Order: no_abstract -> stale -> exclude -> include. The first
    failing check short-circuits.
    """
    if not item.abstract:
        return Stage1Result(False, "no_abstract")

    now = now or datetime.now(timezone.utc)
    age = now - item.published_at
    if age > timedelta(days=max_age_days):
        return Stage1Result(False, "stale")

    haystack = f"{item.title}\n{item.abstract}".lower()

    for term in keywords.exclude:
        if _matches(term, haystack):
            return Stage1Result(False, f"exclude:{term}")

    for term in keywords.include:
        if _matches(term, haystack):
            return Stage1Result(True, f"include:{term}")

    return Stage1Result(False, "no_include_match")
