"""
LLM Client - Abstracts LLM API calls for intelligent analysis.

Supports multiple providers with a unified interface.

Author: Alex (ARCHITECT) - LLM Enhancement
"""

import json
import logging
from typing import Optional, Dict, Any, List
from enum import Enum

import aiohttp
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """Supported LLM providers"""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    MOCK = "mock"  # For testing without API


class LLMConfig(BaseModel):
    """LLM configuration"""
    provider: LLMProvider = LLMProvider.ANTHROPIC
    api_key: str
    model: str = "claude-3-5-sonnet-20241022"  # Latest Claude
    max_tokens: int = 2048
    temperature: float = 0.0  # Deterministic for production
    timeout: int = 30


class LLMResponse(BaseModel):
    """Standardized LLM response"""
    content: str
    provider: str
    model: str
    tokens_used: Optional[int] = None
    finish_reason: Optional[str] = None


class LLMClient:
    """
    Unified LLM client supporting multiple providers.

    **Usage:**
    ```python
    client = LLMClient(config)
    response = await client.complete(prompt, system_prompt)
    ```
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self.provider = config.provider

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None  # "json" for structured output
    ) -> LLMResponse:
        """
        Complete a prompt using the configured LLM.

        Args:
            prompt: User prompt
            system_prompt: System instructions (optional)
            response_format: "json" for JSON mode (optional)

        Returns:
            LLMResponse with content and metadata
        """
        if self.provider == LLMProvider.ANTHROPIC:
            return await self._anthropic_complete(prompt, system_prompt, response_format)
        elif self.provider == LLMProvider.OPENAI:
            return await self._openai_complete(prompt, system_prompt, response_format)
        elif self.provider == LLMProvider.MOCK:
            return await self._mock_complete(prompt, system_prompt)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def _anthropic_complete(
        self,
        prompt: str,
        system_prompt: Optional[str],
        response_format: Optional[str]
    ) -> LLMResponse:
        """Call Anthropic API (Claude)"""
        url = "https://api.anthropic.com/v1/messages"

        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        # Build messages
        messages = [{"role": "user", "content": prompt}]

        # Add JSON instruction if requested
        if response_format == "json":
            messages[0]["content"] = (
                f"{prompt}\n\n"
                "Respond with valid JSON only, no markdown formatting."
            )

        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "messages": messages
        }

        if system_prompt:
            payload["system"] = system_prompt

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"Anthropic API error {response.status}: {error_text}")

                    data = await response.json()

                    content = data["content"][0]["text"]
                    usage = data.get("usage", {})

                    return LLMResponse(
                        content=content,
                        provider="anthropic",
                        model=self.config.model,
                        tokens_used=usage.get("output_tokens"),
                        finish_reason=data.get("stop_reason")
                    )

        except aiohttp.ClientError as e:
            logger.error(f"Anthropic API connection error: {e}")
            raise
        except Exception as e:
            logger.error(f"Anthropic API error: {e}", exc_info=True)
            raise

    async def _openai_complete(
        self,
        prompt: str,
        system_prompt: Optional[str],
        response_format: Optional[str]
    ) -> LLMResponse:
        """Call OpenAI API (GPT)"""
        url = "https://api.openai.com/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json"
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature
        }

        # JSON mode for GPT-4
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"OpenAI API error {response.status}: {error_text}")

                    data = await response.json()

                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})

                    return LLMResponse(
                        content=content,
                        provider="openai",
                        model=self.config.model,
                        tokens_used=usage.get("completion_tokens"),
                        finish_reason=data["choices"][0].get("finish_reason")
                    )

        except aiohttp.ClientError as e:
            logger.error(f"OpenAI API connection error: {e}")
            raise
        except Exception as e:
            logger.error(f"OpenAI API error: {e}", exc_info=True)
            raise

    async def _mock_complete(
        self,
        prompt: str,
        system_prompt: Optional[str]
    ) -> LLMResponse:
        """Mock LLM for testing"""
        # Simple mock responses based on keywords
        if "classify" in prompt.lower():
            content = json.dumps({
                "category": "db_connectivity",
                "confidence": 85.0,
                "reasoning": "Mock: Database connection patterns detected"
            })
        elif "root cause" in prompt.lower():
            content = "Mock: Database connection pool exhaustion due to configuration issue"
        elif "fix" in prompt.lower():
            content = json.dumps([
                {"priority": 1, "action": "Mock: Increase connection pool size"}
            ])
        else:
            content = "Mock LLM response"

        return LLMResponse(
            content=content,
            provider="mock",
            model="mock-model",
            tokens_used=100,
            finish_reason="stop"
        )
