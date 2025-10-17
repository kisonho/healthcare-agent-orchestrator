# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Utilities for integrating a locally hosted LLM with Semantic Kernel.

The `LocalChatCompletion` class provides a simple HTTP client that expects an
OpenAI-compatible chat completions endpoint. If your local deployment uses a
different schema, adjust `_build_payload` and `_parse_response` accordingly.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncGenerator
from typing import Any, ClassVar

import aiohttp
from pydantic import BaseModel, Field, ValidationError

from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase
from semantic_kernel.connectors.ai.prompt_execution_settings import PromptExecutionSettings
from semantic_kernel.contents.chat_history import ChatHistory
from semantic_kernel.contents.chat_message_content import ChatMessageContent
from semantic_kernel.contents.text_content import TextContent
from semantic_kernel.contents.utils.author_role import AuthorRole
from semantic_kernel.contents.utils.finish_reason import FinishReason
from semantic_kernel.exceptions.service_exceptions import (
    ServiceInitializationError,
    ServiceInvalidExecutionSettingsError,
    ServiceInvalidResponseError,
)


class LocalLLMPromptExecutionSettings(PromptExecutionSettings):
    """Prompt execution settings for the local chat completion service."""

    ai_model_id: str | None = Field(default=None, alias="model")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1)
    stop: str | list[str] | None = None
    stream: bool = False
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    response_format: dict[str, Any] | None = None
    extra_headers: dict[str, str] | None = Field(default=None, exclude=True)
    extra_query: dict[str, Any] | None = Field(default=None, exclude=True)
    extra_body: dict[str, Any] | None = Field(default=None, exclude=True)


class LocalChatCompletion(ChatCompletionClientBase):
    """Minimal chat completion client for locally hosted models."""

    MODEL_PROVIDER_NAME: ClassVar[str] = "local"
    SUPPORTS_FUNCTION_CALLING: ClassVar[bool] = False

    endpoint: str
    api_key: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict)
    request_timeout: float | None = Field(default=60.0, ge=0.0)
    verify_ssl: bool = True

    async def _inner_get_chat_message_contents(
        self,
        chat_history: ChatHistory,
        settings: PromptExecutionSettings,
    ) -> list[ChatMessageContent]:
        if not isinstance(settings, LocalLLMPromptExecutionSettings):
            settings = self.get_prompt_execution_settings_from_settings(settings)
        assert isinstance(settings, LocalLLMPromptExecutionSettings)  # nosec

        payload = self._build_payload(chat_history, settings)
        headers = self._build_headers(settings)
        query = settings.extra_query or {}

        response_json = await self._post_json(payload=payload, headers=headers, query=query)
        return self._parse_response(response_json, settings)

    async def _inner_get_streaming_chat_message_contents(
        self,
        chat_history: ChatHistory,
        settings: PromptExecutionSettings,
        function_invoke_attempt: int = 0,
    ) -> AsyncGenerator[list[ChatMessageContent], Any]:
        raise NotImplementedError("Streaming responses are not supported by the local connector.")

    def get_prompt_execution_settings_class(self) -> type[PromptExecutionSettings]:
        return LocalLLMPromptExecutionSettings

    def service_url(self) -> str | None:
        return self.endpoint

    def _build_payload(
        self,
        chat_history: ChatHistory,
        settings: LocalLLMPromptExecutionSettings,
    ) -> dict[str, Any]:
        messages = self._prepare_chat_history_for_request(chat_history)
        payload = {"messages": messages, "model": settings.ai_model_id or self.ai_model_id}

        prepared_settings = settings.prepare_settings_dict()
        # Remove fields we have already handled or that should not go to the body.
        for field_name in ("model", "extra_headers", "extra_query"):
            prepared_settings.pop(field_name, None)

        extra_body = settings.extra_body or {}
        payload.update(prepared_settings)
        payload.update(extra_body)
        return payload

    def _build_headers(self, settings: LocalLLMPromptExecutionSettings) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.default_headers}
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        if settings.extra_headers:
            headers.update(settings.extra_headers)
        return headers

    async def _post_json(
        self,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
        query: dict[str, Any],
    ) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=self.request_timeout) if self.request_timeout else None
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                self.endpoint,
                headers=headers,
                params=query,
                json=payload,
                ssl=self.verify_ssl,
            ) as response:
                body = await response.text()
                if response.status >= 400:
                    raise ServiceInvalidResponseError(
                        f"Local LLM request failed with status {response.status}: {body}"
                    )
                try:
                    return json.loads(body)
                except json.JSONDecodeError as exc:  # pragma: no cover - depends on upstream service
                    raise ServiceInvalidResponseError("Local LLM did not return valid JSON.") from exc

    def _parse_response(
        self,
        response_json: dict[str, Any],
        settings: LocalLLMPromptExecutionSettings,
    ) -> list[ChatMessageContent]:
        choices = response_json.get("choices")
        if not isinstance(choices, list) or len(choices) == 0:
            raise ServiceInvalidResponseError("Local LLM response does not contain choices.")

        ai_model_id = response_json.get("model") or settings.ai_model_id or self.ai_model_id

        completions: list[ChatMessageContent] = []
        for choice in choices:
            message = choice.get("message") if isinstance(choice, dict) else None
            if not isinstance(message, dict):
                raise ServiceInvalidResponseError("Choice does not contain a message object.")

            role = message.get("role", "assistant")
            content = message.get("content", "")
            metadata = {
                "local_llm": True,
                "choice_index": choice.get("index"),
                "finish_reason": choice.get("finish_reason"),
                "id": response_json.get("id"),
            }
            if usage := response_json.get("usage"):
                metadata["usage"] = usage

            finish_reason_value = choice.get("finish_reason")
            finish_reason = None
            if isinstance(finish_reason_value, str):
                try:
                    finish_reason = FinishReason(finish_reason_value)
                except ValueError:
                    finish_reason = None

            completions.append(
                ChatMessageContent(
                    role=AuthorRole(role),
                    items=[TextContent(text=content)] if content else [],
                    content=content or None,
                    ai_model_id=ai_model_id,
                    metadata=metadata,
                    finish_reason=finish_reason,
                )
            )

        return completions


def _load_json_env(env_var: str) -> dict[str, Any] | None:
    value = os.getenv(env_var)
    if not value:
        return None

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ServiceInitializationError(f"{env_var} must be valid JSON.") from exc

    if not isinstance(parsed, dict):
        raise ServiceInitializationError(f"{env_var} must contain a JSON object.")
    return parsed


def _load_bool_env(env_var: str, default: bool) -> bool:
    value = os.getenv(env_var)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_response_format(response_format: Any) -> Any:
    """Convert response_format values to the JSON schema dict expected by the local connector."""
    if isinstance(response_format, type) and issubclass(response_format, BaseModel):
        schema = response_format.model_json_schema()
        return {
            "type": "json_schema",
            "json_schema": {
                "name": response_format.__name__,
                "schema": schema,
            },
        }

    if isinstance(response_format, BaseModel):
        return _normalize_response_format(response_format.__class__)

    return response_format


def create_local_chat_completion_service(*, service_id: str, ai_model_id: str | None = None) -> LocalChatCompletion:
    """Factory used by group_chat to build the local chat completion client."""

    model_id = (
        ai_model_id
        or os.getenv("LOCAL_LLM_MODEL_ID")
        or os.getenv("LLM_MODEL_ID")
        or os.getenv("OPENAI_MODEL_NAME")
        or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    )
    if not model_id:
        raise ServiceInitializationError(
            "LOCAL_LLM_MODEL_ID (or LLM_MODEL_ID/OPENAI_MODEL_NAME/AZURE_OPENAI_DEPLOYMENT_NAME) must be set "
            "when LLM_PROVIDER=local."
        )

    endpoint = os.getenv("LOCAL_LLM_ENDPOINT", "http://localhost:8000/v1/chat/completions")
    api_key = os.getenv("LOCAL_LLM_API_KEY")
    default_headers = _load_json_env("LOCAL_LLM_HEADERS") or {}
    timeout_env = os.getenv("LOCAL_LLM_REQUEST_TIMEOUT")
    timeout = float(timeout_env) if timeout_env else 60.0
    verify_ssl = _load_bool_env("LOCAL_LLM_VERIFY_SSL", True)

    try:
        return LocalChatCompletion(
            service_id=service_id or model_id,
            ai_model_id=model_id,
            endpoint=endpoint,
            api_key=api_key,
            default_headers=default_headers,
            request_timeout=timeout,
            verify_ssl=verify_ssl,
        )
    except ValidationError as exc:
        raise ServiceInitializationError("Invalid configuration for LocalChatCompletion.") from exc


def create_local_prompt_settings(**kwargs: Any) -> LocalLLMPromptExecutionSettings:
    """Factory used by group_chat to build local prompt execution settings."""

    normalized_kwargs = dict(kwargs)
    if "response_format" in normalized_kwargs and normalized_kwargs["response_format"] is not None:
        normalized_kwargs["response_format"] = _normalize_response_format(normalized_kwargs["response_format"])

    try:
        return LocalLLMPromptExecutionSettings(**normalized_kwargs)
    except ValidationError as exc:
        raise ServiceInvalidExecutionSettingsError("Invalid local LLM prompt settings.") from exc
