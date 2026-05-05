"""Pipeline orchestrator.

Stage 1 (fetch + filter) -> Stage 2 (relevance) -> Stage 3 (summarize).
Stages 2-3 use the LLMClient abstraction (default OpenRouter). Drafts
land in the `drafts` table; per-call usage lands in `cost_log`.

`limit` caps how many items get scored / summarized this run, so
acceptance tests can exercise N=10 without burning the whole queue.
"""

import logging
from pathlib import Path
from typing import Iterable

from channels_operator.config.loader import load_channel, load_keywords
from channels_operator.llm.factory import build_client
from channels_operator.pipeline.stage1_filter import stage1_filter
from channels_operator.pipeline.stage2_relevance import stage2_score
from channels_operator.pipeline.stage3_summarize import (
    format_lexicon,
    stage3_summarize_item,
)
from channels_operator.sources.loader import load_sources
from channels_operator.storage.repository import (
    get_stage2_passed_without_draft,
    get_stage2_pending,
    log_cost,
    save_draft,
    save_items_idempotent,
    update_stage2_outcome,
)

logger = logging.getLogger(__name__)


VALID_STAGES = ("stage1", "stage2", "stage3")


def run_pipeline(
    channel_id: str,
    channels_root: Path,
    *,
    limit: int | None = None,
    stages: Iterable[str] = VALID_STAGES,
) -> dict:
    """Run the configured stages once for a channel."""
    channel_dir = channels_root / channel_id
    channel = load_channel(channel_dir)
    stages = tuple(stages)

    result: dict = {
        "channel_id": channel.id,
        "stage1": {"sources": {}},
        "stage2": {"scored": 0, "passed": 0, "errors": 0},
        "stage3": {"summarized": 0, "errors": 0, "drafts": []},
    }

    if "stage1" in stages:
        keywords = load_keywords(channel_dir / channel.keywords_file)
        sources = load_sources(channel_dir / channel.sources_file)
        for source in sources:
            try:
                raws = source.fetch()
            except Exception as exc:
                logger.exception("Source %s fetch failed", source.name)
                result["stage1"]["sources"][source.name] = {
                    "fetched": 0,
                    "stage1_pass": 0,
                    "stored_new": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                continue
            results = [stage1_filter(item, keywords) for item in raws]
            passed = sum(1 for r in results if r.passed)
            stored = save_items_idempotent(raws, results, channel_id=channel.id)
            result["stage1"]["sources"][source.name] = {
                "fetched": len(raws),
                "stage1_pass": passed,
                "stored_new": stored,
                "error": None,
            }
            logger.info(
                "%s: fetched=%d stage1=%d stored_new=%d",
                source.name, len(raws), passed, stored,
            )

    if "stage2" in stages:
        items = get_stage2_pending(channel.id, limit=limit)
        if items:
            client = build_client(channel.models.stage2.provider)
            relevance_prompt = (channel_dir / channel.prompts.relevance).read_text(
                encoding="utf-8"
            )
            for item in items:
                try:
                    decision = stage2_score(item, channel, client, relevance_prompt)
                    update_stage2_outcome(
                        item_id=item.id,
                        passed=decision.passed,
                        score=decision.score,
                        axis=decision.axis,
                        topic_tag=decision.topic_tag,
                        audience_fit=decision.audience_fit,
                        rationale=decision.rationale,
                    )
                    log_cost(
                        channel_id=channel.id,
                        item_id=item.id,
                        stage="stage2",
                        model=decision.usage["model"],
                        prompt_tokens=decision.usage["prompt_tokens"],
                        completion_tokens=decision.usage["completion_tokens"],
                    )
                    result["stage2"]["scored"] += 1
                    if decision.passed:
                        result["stage2"]["passed"] += 1
                    logger.info(
                        "stage2 item=%d score=%d audience=%s axis=%s passed=%s",
                        item.id,
                        decision.score,
                        decision.audience_fit,
                        decision.axis,
                        decision.passed,
                    )
                except Exception:
                    logger.exception("Stage 2 failed for item %d", item.id)
                    result["stage2"]["errors"] += 1

    if "stage3" in stages:
        items = get_stage2_passed_without_draft(channel.id, limit=limit)
        if items:
            client = build_client(channel.models.stage3.provider)
            summarize_template = (channel_dir / channel.prompts.summarize).read_text(
                encoding="utf-8"
            )
            lexicon_text = format_lexicon(channel_dir / channel.lexicon_file)
            for item in items:
                try:
                    out = stage3_summarize_item(
                        item, channel, client, summarize_template, lexicon_text,
                    )
                    log_cost(
                        channel_id=channel.id,
                        item_id=item.id,
                        stage="stage3",
                        model=out.usage["model"],
                        prompt_tokens=out.usage["prompt_tokens"],
                        completion_tokens=out.usage["completion_tokens"],
                    )
                    if not out.post_body:
                        logger.info(
                            "stage3 item=%d returned empty body; skipping draft save",
                            item.id,
                        )
                        continue
                    draft_id = save_draft(
                        item_id=item.id,
                        channel_id=channel.id,
                        post_body=out.post_body,
                        hashtags=out.hashtags,
                        headline_finding=out.headline_finding,
                        methodological_caveat=out.methodological_caveat,
                    )
                    result["stage3"]["drafts"].append(
                        {
                            "draft_id": draft_id,
                            "item_id": item.id,
                            "title": item.title,
                            "source": item.source,
                            "url": item.url,
                            "axis": item.stage2_axis,
                            "topic_tag": item.stage2_topic_tag,
                            "score": item.stage2_score,
                            "post_body": out.post_body,
                            "hashtags": out.hashtags,
                            "headline_finding": out.headline_finding,
                            "methodological_caveat": out.methodological_caveat,
                        }
                    )
                    result["stage3"]["summarized"] += 1
                except Exception:
                    logger.exception("Stage 3 failed for item %d", item.id)
                    result["stage3"]["errors"] += 1

    return result
