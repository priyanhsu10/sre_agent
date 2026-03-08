"""
LLM Client - Custom OpenAI-compatible provider via langchain-openai.

Only supports OpenAI-compatible endpoints (custom/internal self-hosted models).
Set LLM_BASE_URL to your endpoint, e.g. http://internal-llm.company.com/v1
"""

import logging
from typing import Optional

import httpx
from pydantic import BaseModel
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


class LLMConfig(BaseModel):
    """LLM configuration for a custom OpenAI-compatible endpoint."""
    api_key: str
    model: str = "gpt-4o"
    max_tokens: int = 2048
    temperature: float = 0.0
    timeout: int = 30
    base_url: str = ""  # e.g. http://internal-llm.company.com/v1


class LLMResponse(BaseModel):
    """Standardized LLM response."""
    content: str
    model: str
    tokens_used: Optional[int] = None
    finish_reason: Optional[str] = None


class LLMClient:
    """
    LLM client for custom OpenAI-compatible endpoints.

    Uses langchain-openai under the hood.  The endpoint must expose
    /chat/completions in the OpenAI API format (vLLM, LM Studio, Ollama, etc.).

    Usage:
        config = LLMConfig(api_key="...", base_url="http://my-llm/v1", model="my-model")
        client = LLMClient(config)
        response = await client.complete(prompt, system_prompt)
    """

    def __init__(self, config: LLMConfig):
        if not config.base_url:
            raise ValueError(
                "LLM_BASE_URL must be set. "
                "Example: http://internal-llm.company.com/v1"
            )
        self.config = config
        # Disable SSL verification for corporate networks with self-signed certs
        self._llm = ChatOpenAI(
            api_key=config.api_key,
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            request_timeout=config.timeout,
            base_url=config.base_url,
            http_client=httpx.AsyncClient(verify=False),
        )

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> LLMResponse:
        """
        Complete a prompt using the custom LLM endpoint.

        Args:
            prompt: User prompt
            system_prompt: System instructions (optional)
            response_format: "json" to request JSON output

        Returns:
            LLMResponse with content and metadata
        """
        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))

        user_content = prompt
        if response_format == "json":
            user_content += "\n\nRespond with valid JSON only, no markdown formatting."
        messages.append(HumanMessage(content=user_content))

        # Attempt to bind JSON mode (supported by many OpenAI-compatible servers)
        llm = self._llm
        if response_format == "json":
            try:
                llm = self._llm.bind(response_format={"type": "json_object"})
            except Exception:
                pass  # Fall back to prompt-based JSON instruction

        logger.debug(f"LLM request → {self.config.base_url}  model={self.config.model}")

        response = await llm.ainvoke(messages)

        # Extract token usage if the server returns it
        tokens_used = None
        usage = getattr(response, "usage_metadata", None)
        if isinstance(usage, dict):
            tokens_used = usage.get("output_tokens")

        return LLMResponse(
            content=response.content,
            model=self.config.model,
            tokens_used=tokens_used,
        )
