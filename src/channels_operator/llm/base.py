"""LLMClient protocol shared across all providers.

Stage 2 calls `score()`; Stage 3 calls `summarize()`. The protocol is the
seam through which we A/B models, swap providers, or fall back during
outages — no caller cares which backend serves the request.

SaaS-readiness: this interface is what makes per-tenant model overrides
cheap. Each tenant's channel.yaml picks `provider` and `model`; the
factory builds the right client; the pipeline calls the same protocol.

Sync API for v1 simplicity. The orchestrator is sync; if it moves to
async (Phase 5+), wrap calls with asyncio.to_thread.
"""

from typing import Any, Protocol


class LLMClient(Protocol):
    def score(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int = 500,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Stage 2: cheap relevance scoring. Returns (parsed_json, usage_record)."""
        ...

    def summarize(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int = 1500,
        reasoning: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Stage 3: lexicon-disciplined summarization.

        `reasoning=False` adds the OpenRouter reasoning-disable extra_body
        flag. Required for Qwen and other reasoning models that would
        otherwise burn reasoning tokens on this task.
        """
        ...
