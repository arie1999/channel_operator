"""OpenRouterClient: default LLM client.

OpenRouter is OpenAI-compatible, so the OpenAI Python SDK works as-is
with `base_url` set. Models are addressed with provider prefix:
`anthropic/claude-haiku-4-5`, `qwen/qwen3.6-plus`, etc.
"""

import json
import logging
import time
from typing import Any

from openai import APIConnectionError, APIError, OpenAI, RateLimitError

logger = logging.getLogger(__name__)

_BASE_URL = "https://openrouter.ai/api/v1"
_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (1, 3, 7)


def _parse_json_response(text: str) -> dict:
    """Tolerate accidental code fences. Fail loudly on malformed JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    return json.loads(text.strip())


class OpenRouterClient:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required")
        self._client = OpenAI(base_url=_BASE_URL, api_key=api_key)

    def score(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int = 500,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            max_tokens=max_tokens,
            reasoning=False,
            stage="stage2",
        )

    def summarize(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int = 1500,
        reasoning: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return self._call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            max_tokens=max_tokens,
            reasoning=reasoning,
            stage="stage3",
        )

    def _call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int,
        reasoning: bool,
        stage: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}
        if not reasoning:
            extra_body["reasoning"] = {"enabled": False}

        last_exc: Exception | None = None
        attempts = (*_RETRY_BACKOFF_SECONDS, 0)
        for attempt, wait in enumerate(attempts):
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    extra_headers={
                        "HTTP-Referer": "https://github.com/channels-operator",
                        "X-Title": "Channels Operator",
                    },
                    extra_body=extra_body,
                )
                text = response.choices[0].message.content or ""
                parsed = _parse_json_response(text)
                usage = {
                    "model": model,
                    "stage": stage,
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                }
                return parsed, usage
            except (APIConnectionError, RateLimitError, json.JSONDecodeError) as exc:
                last_exc = exc
                if wait <= 0:
                    break
                logger.warning(
                    "%s attempt %d on %s failed: %s. Retrying in %ds",
                    stage, attempt + 1, model, exc, wait,
                )
                time.sleep(wait)
            except APIError as exc:
                # Non-retryable server-side errors (bad request, auth, ...)
                logger.warning("%s on %s failed (no retry): %s", stage, model, exc)
                raise

        assert last_exc is not None
        raise last_exc
