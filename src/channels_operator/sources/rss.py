"""Generic RSS feed source. feedparser-backed."""

import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

from .base import RawItem

logger = logging.getLogger(__name__)


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text or "").strip()


def _parse_date(entry) -> datetime | None:
    for key in ("published", "updated", "created"):
        v = entry.get(key)
        if not v:
            continue
        try:
            dt = parsedate_to_datetime(v)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (TypeError, ValueError):
            continue
    for key in ("published_parsed", "updated_parsed"):
        v = entry.get(key)
        if v:
            return datetime(*v[:6], tzinfo=timezone.utc)
    return None


def _extract_abstract(entry) -> str:
    for key in ("summary", "description"):
        v = entry.get(key)
        if v:
            return _strip_html(v)
    content = entry.get("content")
    if content and isinstance(content, list):
        return _strip_html(content[0].get("value", ""))
    return ""


class RSSSource:
    def __init__(self, name: str, url: str, axis_hint: str | None = None) -> None:
        self.name = name
        self.url = url
        self.axis_hint = axis_hint

    def fetch(self) -> list[RawItem]:
        feed = feedparser.parse(self.url)
        if feed.bozo and not feed.entries:
            logger.warning(
                "RSS source %s parse error: %s",
                self.name,
                getattr(feed, "bozo_exception", ""),
            )
            return []
        items: list[RawItem] = []
        for entry in feed.entries:
            published = _parse_date(entry)
            if published is None:
                continue
            sid = entry.get("id") or entry.get("guid") or entry.get("link", "")
            if not sid:
                continue
            items.append(
                RawItem(
                    source=self.name,
                    source_id=str(sid),
                    title=(entry.get("title") or "").strip(),
                    abstract=_extract_abstract(entry),
                    url=entry.get("link") or "",
                    published_at=published,
                    axis_hint=self.axis_hint,
                )
            )
        return items
