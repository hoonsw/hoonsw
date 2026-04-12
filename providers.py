#!/usr/bin/env python3
"""
Model provider abstraction for translation and review.

Supports:
- OpenAI (via openai SDK) — primary default
- Claude (via claude-agent-sdk) — optional
- Gemini (via google-genai SDK) — placeholder, interface ready

Config structure in config.yaml:
    providers:
      translation:
        provider: openai          # claude / openai / gemini
        model: gpt-5.2-chat-latest
      review:
        provider: openai
        model: gpt-5.2-chat-latest

Backward compatibility: if providers section is absent, falls back
to top-level 'model' field (existing behavior).
"""

import asyncio
import os
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional


class ProviderResponse:
    """Standardized response from any provider."""
    def __init__(self, text: str, model: str, provider: str,
                 usage: dict = None):
        self.text = text
        self.model = model
        self.provider = provider
        self.usage = usage or {}


class BaseProvider(ABC):
    """Abstract base class for translation/review model providers."""

    def __init__(self, model: str, max_turns: int = 1):
        self.model = model
        self.max_turns = max_turns

    @abstractmethod
    async def generate(self, system_prompt: str, user_prompt: str) -> ProviderResponse:
        """Generate a response given system and user prompts."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier string."""
        ...


class ClaudeProvider(BaseProvider):
    """Claude provider via claude-agent-sdk."""

    def __init__(self, model: str = "claude-sonnet-4-6", max_turns: int = 1):
        super().__init__(model, max_turns)
        # Import at init to fail early if SDK missing
        from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock
        self._query = query
        self._ClaudeAgentOptions = ClaudeAgentOptions
        self._AssistantMessage = AssistantMessage
        self._TextBlock = TextBlock

    async def generate(self, system_prompt: str, user_prompt: str) -> ProviderResponse:
        options = self._ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=self.max_turns,
            allowed_tools=[],
        )
        options.model = self.model

        result_text = ""
        async for message in self._query(prompt=user_prompt, options=options):
            if isinstance(message, self._AssistantMessage):
                for block in message.content:
                    if isinstance(block, self._TextBlock):
                        result_text += block.text

        return ProviderResponse(
            text=result_text.strip(),
            model=self.model,
            provider="claude",
        )

    @property
    def provider_name(self) -> str:
        return "claude"


class OpenAIProvider(BaseProvider):
    """OpenAI provider via openai SDK.

    TODO: Full implementation requires `pip install openai` and API key.
    Interface is stable — implement generate() when SDK is available.
    """

    def __init__(self, model: str = "gpt-5.2-chat-latest", max_turns: int = 1):
        super().__init__(model, max_turns)
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Configure an OpenAI API key before running the translator."
            )
        try:
            import openai
            self._client = openai.AsyncOpenAI()
        except ImportError:
            raise RuntimeError(
                "OpenAI provider requires the openai package. "
                "Install with: pip install openai"
            )

    async def generate(self, system_prompt: str, user_prompt: str) -> ProviderResponse:
        self._ensure_client()
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        text = response.choices[0].message.content or ""
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }
        return ProviderResponse(
            text=text.strip(),
            model=self.model,
            provider="openai",
            usage=usage,
        )

    @property
    def provider_name(self) -> str:
        return "openai"


class GeminiProvider(BaseProvider):
    """Google Gemini provider.

    TODO: Full implementation requires `pip install google-genai` and API key.
    Interface is stable — implement generate() when SDK is available.
    """

    def __init__(self, model: str = "gemini-2.5-pro", max_turns: int = 1):
        super().__init__(model, max_turns)
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            from google import genai
            self._client = genai.Client()
        except ImportError:
            raise RuntimeError(
                "Gemini provider requires the google-genai package. "
                "Install with: pip install google-genai"
            )

    async def generate(self, system_prompt: str, user_prompt: str) -> ProviderResponse:
        self._ensure_client()
        # google-genai async API
        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=self.model,
            contents=user_prompt,
            config={
                "system_instruction": system_prompt,
                "temperature": 0.3,
            },
        )
        text = response.text or ""
        return ProviderResponse(
            text=text.strip(),
            model=self.model,
            provider="gemini",
        )

    @property
    def provider_name(self) -> str:
        return "gemini"


# ── Provider factory ─────────────────────────────────────────

_PROVIDER_MAP = {
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}


def create_provider(provider: str, model: str, max_turns: int = 1) -> BaseProvider:
    """Create a provider instance by name.

    Args:
        provider: One of 'claude', 'openai', 'gemini'
        model: Model identifier string
        max_turns: Max conversation turns (usually 1 for translation)

    Returns:
        BaseProvider instance
    """
    cls = _PROVIDER_MAP.get(provider)
    if cls is None:
        raise ValueError(
            f"Unknown provider: {provider}. "
            f"Available: {', '.join(_PROVIDER_MAP.keys())}"
        )
    return cls(model=model, max_turns=max_turns)


def get_provider_from_config(config: dict, role: str = "translation") -> BaseProvider:
    """Create a provider from config.yaml data.

    Supports both new providers section and legacy top-level model field.

    Args:
        config: Parsed config.yaml dict
        role: 'translation' or 'review'

    Returns:
        BaseProvider instance
    """
    providers_cfg = config.get('providers', {})
    role_cfg = providers_cfg.get(role, {})

    if role_cfg:
        provider_name = role_cfg.get('provider', 'openai')
        model = role_cfg.get('model', config.get('model', 'gpt-5.2-chat-latest'))
    else:
        # Legacy fallback: use top-level model field
        provider_name = config.get('provider', 'openai')
        model = config.get('model', 'gpt-5.2-chat-latest')

    max_turns = config.get('max_turns', 1)
    return create_provider(provider_name, model, max_turns)
