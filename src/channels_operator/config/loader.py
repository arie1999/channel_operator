"""Channel config loader.

Today: reads YAML files from a `channels/` directory tree.

SaaS-readiness: this module is the *interface*. A future
DatabaseConfigLoader will return the same ChannelConfig instances from
a Postgres row, with zero changes needed in callers — the loader is
the only place that knows where channel definitions live.
"""

from pathlib import Path

import yaml

from channels_operator.config.models import ChannelConfig, KeywordsConfig


def load_channel(channel_dir: Path) -> ChannelConfig:
    """Load and validate a single channel.yaml from its folder."""
    yaml_path = channel_dir / "channel.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"No channel.yaml in {channel_dir}")
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return ChannelConfig.model_validate(raw)


def load_keywords(keywords_yaml: Path) -> KeywordsConfig:
    """Load and validate a channel's keywords.yaml."""
    if not keywords_yaml.exists():
        raise FileNotFoundError(f"No keywords.yaml at {keywords_yaml}")
    raw = yaml.safe_load(keywords_yaml.read_text(encoding="utf-8"))
    return KeywordsConfig.model_validate(raw)


def discover_channels(channels_root: Path) -> dict[str, ChannelConfig]:
    """Find every `channels/<name>/channel.yaml` and return them keyed by id.

    Skips subdirectories that lack a channel.yaml. Raises ValueError
    if two channels declare the same `id`.
    """
    found: dict[str, ChannelConfig] = {}
    if not channels_root.exists():
        return found
    for sub in sorted(channels_root.iterdir()):
        if not sub.is_dir():
            continue
        if not (sub / "channel.yaml").exists():
            continue
        cfg = load_channel(sub)
        if cfg.id in found:
            raise ValueError(
                f"Duplicate channel id '{cfg.id}': "
                f"already loaded; conflicting at {sub}"
            )
        found[cfg.id] = cfg
    return found
