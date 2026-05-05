"""Source loader. Reads sources.yaml and instantiates the right Source class."""

from pathlib import Path

import yaml

from .arxiv import ArXivSource
from .base import Source
from .pubmed import PubMedSource
from .rss import RSSSource


def load_sources(sources_yaml: Path) -> list[Source]:
    """Build a list of Source instances from a channel's sources.yaml."""
    if not sources_yaml.exists():
        raise FileNotFoundError(f"No sources.yaml at {sources_yaml}")
    data = yaml.safe_load(sources_yaml.read_text(encoding="utf-8"))
    sources: list[Source] = []
    for entry in data.get("sources", []):
        type_ = entry["type"]
        name = entry["name"]
        axis = entry.get("axis_hint")
        if type_ == "rss":
            sources.append(RSSSource(name=name, url=entry["url"], axis_hint=axis))
        elif type_ in ("pubmed", "pubmed_rss"):
            sources.append(PubMedSource(name=name, query=entry["query"], axis_hint=axis))
        elif type_ == "arxiv":
            sources.append(ArXivSource(name=name, query=entry["query"], axis_hint=axis))
        else:
            raise ValueError(f"Unknown source type '{type_}' for source '{name}'")
    return sources
