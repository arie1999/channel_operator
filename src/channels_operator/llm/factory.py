"""Build the right LLMClient implementation from channel config.

Today: only OpenRouter. Direct Anthropic and OpenAI clients are
scaffolded but empty — only fill them in when there's a concrete reason
to bypass the gateway (cost, latency, fallback during outages).
"""

from channels_operator.llm.base import LLMClient
from channels_operator.llm.openrouter import OpenRouterClient
from channels_operator.settings import settings


def build_client(provider: str) -> LLMClient:
    if provider == "openrouter":
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set in environment")
        return OpenRouterClient(api_key=settings.openrouter_api_key)
    raise ValueError(f"Unknown LLM provider: {provider!r}")
