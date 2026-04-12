from __future__ import annotations

from providers import BaseProvider, create_provider
from translator_v2.core.models import V2Config


def build_provider(config: V2Config) -> BaseProvider:
    return create_provider(config.provider_name, config.model, config.max_turns)
