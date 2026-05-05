"""arXiv source. Uses the `arxiv` library."""

import logging

import arxiv

from .base import RawItem

logger = logging.getLogger(__name__)


class ArXivSource:
    def __init__(
        self,
        name: str,
        query: str,
        axis_hint: str | None = None,
        max_results: int = 50,
    ) -> None:
        self.name = name
        self.query = query
        self.axis_hint = axis_hint
        self.max_results = max_results

    def fetch(self) -> list[RawItem]:
        client = arxiv.Client(num_retries=3, page_size=100)
        search = arxiv.Search(
            query=self.query,
            max_results=self.max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        items: list[RawItem] = []
        try:
            for result in client.results(search):
                items.append(
                    RawItem(
                        source=self.name,
                        source_id=result.entry_id,
                        title=(result.title or "").strip(),
                        abstract=(result.summary or "").strip(),
                        url=result.entry_id,
                        published_at=result.published,
                        axis_hint=self.axis_hint,
                    )
                )
        except Exception as exc:
            logger.warning("arXiv %s fetch error: %s", self.name, exc)
        return items
