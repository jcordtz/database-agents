"""Thin wrapper around Azure OpenAI chat completions."""
from __future__ import annotations

import json
from typing import Any

from openai import AzureOpenAI

from db_agents.config import LLMConfig


class LLMClient:
    """Wraps an Azure OpenAI chat client with simple text and JSON helpers."""

    def __init__(self, config: LLMConfig):
        self._config = config
        self._client = AzureOpenAI(
            azure_endpoint=config.endpoint,
            api_key=config.api_key,
            api_version=config.api_version,
        )

    def complete(self, system: str, user: str, temperature: float = 0.2, max_tokens: int = 1500) -> str:
        response = self._client.chat.completions.create(
            model=self._config.deployment,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    def complete_json(self, system: str, user: str, temperature: float = 0.0, max_tokens: int = 1500) -> Any:
        """Request a JSON object response and parse it."""
        response = self._client.chat.completions.create(
            model=self._config.deployment,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
