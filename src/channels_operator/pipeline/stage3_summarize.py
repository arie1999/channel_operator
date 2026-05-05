"""Stage 3: lexicon-disciplined Hebrew summarization.

The lexicon (channel.yaml.lexicon_file) is rendered into the {lexicon}
placeholder in the summarize prompt. The Hebrew output comes back as
JSON: post_body, hashtags, headline_finding, methodological_caveat.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from channels_operator.config.models import ChannelConfig
from channels_operator.llm.base import LLMClient
from channels_operator.storage.models import Item

logger = logging.getLogger(__name__)


@dataclass
class Stage3Output:
    item_id: int
    post_body: str
    hashtags: list[str]
    headline_finding: str
    methodological_caveat: str
    usage: dict


def format_lexicon(lexicon_yaml_path: Path) -> str:
    """Render lexicon.yaml entries as the inline list the prompt expects.

    Schema:
      terms:
        - en: <english>
          he: <hebrew>
          forbid: <hebrew, optional>   # rendered as "(NOT X)"
          note:   <text, optional>     # rendered as "[note]"
    """
    data = yaml.safe_load(lexicon_yaml_path.read_text(encoding="utf-8"))
    lines: list[str] = []
    for t in data.get("terms", []):
        line = f"- {t['en']} → {t['he']}"
        if t.get("forbid"):
            line += f"  (NOT {t['forbid']})"
        if t.get("note"):
            line += f"  [{t['note']}]"
        lines.append(line)
    return "\n".join(lines)


def _format_user_message(item: Item) -> str:
    return (
        "ITEM TO SUMMARIZE\n"
        f"Title: {item.title}\n"
        f"Source: {item.source}\n"
        f"Axis: {item.axis_hint or '(none)'}\n"
        "Abstract:\n"
        f"{item.abstract}"
    )


def stage3_summarize_item(
    item: Item,
    channel: ChannelConfig,
    client: LLMClient,
    summarize_prompt_template: str,
    lexicon_text: str,
) -> Stage3Output:
    system_prompt = summarize_prompt_template.replace("{lexicon}", lexicon_text)
    parsed, usage = client.summarize(
        system_prompt=system_prompt,
        user_prompt=_format_user_message(item),
        model=channel.models.stage3.model,
        reasoning=channel.models.stage3.reasoning,
    )
    return Stage3Output(
        item_id=item.id,
        post_body=parsed.get("post_body") or "",
        hashtags=parsed.get("hashtags") or [],
        headline_finding=parsed.get("headline_finding") or "",
        methodological_caveat=parsed.get("methodological_caveat") or "",
        usage=usage,
    )
