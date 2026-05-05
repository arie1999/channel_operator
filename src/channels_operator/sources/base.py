"""Source protocol and the canonical RawItem shape.

Each source implementation (RSS, PubMed, arXiv, ...) returns a list of
RawItems with a uniform schema. Stage 1 and downstream stages do not
care which source a RawItem came from — only what's in it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


@dataclass
class RawItem:
    """An item fetched from a source, before any filtering or AI scoring."""

    source: str
    source_id: str
    title: str
    abstract: str
    url: str
    published_at: datetime  # tz-aware
    axis_hint: Optional[str] = None  # "A" or "B" from sources.yaml


class Source(Protocol):
    """Source contract. Implementations are sync; the orchestrator can
    parallelize via a thread pool when that becomes worthwhile."""

    name: str
    axis_hint: Optional[str]

    def fetch(self) -> list[RawItem]:
        ...
