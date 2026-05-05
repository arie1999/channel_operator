"""PubMed source via NCBI eutils (esearch + efetch).

We use eutils rather than the raw RSS feed because the RSS feed often
omits abstracts. esearch returns PMIDs matching the query; efetch
returns full XML records including AbstractText elements.
"""

import logging
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import httpx

from .base import RawItem

logger = logging.getLogger(__name__)

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _month_to_int(m: str | None) -> int:
    if not m:
        return 1
    if m.isdigit():
        try:
            return max(1, min(12, int(m)))
        except ValueError:
            return 1
    return _MONTH_MAP.get(m.lower()[:3], 1)


def _parse_pubmed_date(article: ET.Element) -> datetime:
    for path in (".//ArticleDate", ".//PubDate"):
        d = article.find(path)
        if d is None:
            continue
        year = d.findtext("Year")
        if not year:
            mdl = d.findtext("MedlineDate") or ""
            year = mdl.split()[0] if mdl else ""
        if year and year.isdigit():
            try:
                return datetime(
                    int(year),
                    _month_to_int(d.findtext("Month")),
                    int(d.findtext("Day") or 1),
                    tzinfo=timezone.utc,
                )
            except (ValueError, TypeError):
                continue
    return datetime.now(timezone.utc)


class PubMedSource:
    """Fetches recent items matching `query`. Both `pubmed` and
    `pubmed_rss` types in sources.yaml map here."""

    def __init__(
        self,
        name: str,
        query: str,
        axis_hint: str | None = None,
        retmax: int = 50,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.query = query
        self.axis_hint = axis_hint
        self.retmax = retmax
        self.timeout = timeout

    def fetch(self) -> list[RawItem]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                ids = self._esearch(client)
                if not ids:
                    return []
                return self._efetch(client, ids)
        except httpx.HTTPError as exc:
            logger.warning("PubMed %s HTTP error: %s", self.name, exc)
            return []
        except ET.ParseError as exc:
            logger.warning("PubMed %s XML parse error: %s", self.name, exc)
            return []

    def _esearch(self, client: httpx.Client) -> list[str]:
        r = client.get(
            f"{_BASE}/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": self.query,
                "retmax": str(self.retmax),
                "sort": "date",
            },
        )
        r.raise_for_status()
        root = ET.fromstring(r.text)
        return [e.text for e in root.findall(".//Id") if e.text]

    def _efetch(self, client: httpx.Client, ids: list[str]) -> list[RawItem]:
        r = client.get(
            f"{_BASE}/efetch.fcgi",
            params={
                "db": "pubmed",
                "id": ",".join(ids),
                "rettype": "abstract",
                "retmode": "xml",
            },
        )
        r.raise_for_status()
        root = ET.fromstring(r.text)

        items: list[RawItem] = []
        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//PMID")
            if not pmid:
                continue
            title = (article.findtext(".//ArticleTitle") or "").strip()

            abstract_parts: list[str] = []
            for at in article.findall(".//Abstract/AbstractText"):
                label = at.get("Label")
                text = "".join(at.itertext()).strip()
                if not text:
                    continue
                abstract_parts.append(f"{label}. {text}" if label else text)
            abstract = " ".join(abstract_parts)

            published = _parse_pubmed_date(article)

            items.append(
                RawItem(
                    source=self.name,
                    source_id=pmid,
                    title=title,
                    abstract=abstract,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    published_at=published,
                    axis_hint=self.axis_hint,
                )
            )
        return items
