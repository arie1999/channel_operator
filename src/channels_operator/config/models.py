"""Pydantic models for channel configuration.

Each channel folder under `channels/` contains a `channel.yaml` that
deserializes into ChannelConfig. Source / keyword / lexicon files are
referenced from channel.yaml and loaded separately by the loader.
"""

from typing import Literal

from pydantic import BaseModel, Field


class AxisConfig(BaseModel):
    id: str
    label: str
    hashtags: list[str]
    target_share: float = Field(ge=0.0, le=1.0)


class ModelEndpointConfig(BaseModel):
    provider: Literal["openrouter", "anthropic", "openai"] = "openrouter"
    model: str
    reasoning: bool = False


class ModelsConfig(BaseModel):
    stage2: ModelEndpointConfig
    stage3: ModelEndpointConfig


class CadenceConfig(BaseModel):
    posts_per_week_target: int
    drafts_per_day_max: int
    fetch_time_local: str
    publish_times_local: list[str]
    timezone: str


class PromptsConfig(BaseModel):
    relevance: str
    summarize: str


class ChannelConfig(BaseModel):
    id: str
    name: str
    language: str
    telegram_channel_id: str
    audience: str
    voice: str
    axes: list[AxisConfig]
    # Optional emoji prepended to draft DM headers — helps the operator
    # scan multiple channels at a glance (per BRIEF.md §4).
    emoji: str = ""
    editor_bot_token: str = ""
    publisher_bot_token: str = ""
    models: ModelsConfig
    sources_file: str
    keywords_file: str
    lexicon_file: str
    prompts: PromptsConfig
    cadence: CadenceConfig


class KeywordsConfig(BaseModel):
    """Stage 1 keyword filter. An item passes if it contains at least
    one `include` term and no `exclude` term (whole-token,
    case-insensitive)."""

    include: list[str]
    exclude: list[str]
