# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import os
from typing import Any, Callable

from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from autogen_agentchat.base import ChatAgent
from autogen_agentchat.teams import MagenticOneGroupChat
from semantic_kernel.agents import Agent, AgentGroupChat

from data_models.app_context import AppContext
from services import Provider, get_llm_provider

try:
    import autogen_ext.models.openai as autogen_openai
    from autogen_ext.models.openai import AzureOpenAIChatCompletionClient, OpenAIChatCompletionClient
except ImportError:  # pragma: no cover - optional dependency
    autogen_openai = None  # type: ignore[assignment]


def _load_factory(factory_spec: str) -> Callable[..., Any]:
    module_path, _, factory_name = factory_spec.partition(":")
    if not module_path or not factory_name:
        raise ValueError("Factory specifications must be formatted as 'package.module:function_name'.")

    factory_module = importlib.import_module(module_path)
    factory = getattr(factory_module, factory_name, None)
    if factory is None or not callable(factory):
        raise AttributeError(f"Factory function '{factory_name}' not found or not callable in module '{module_path}'.")
    return factory


def _create_default_model_client(app_context: AppContext):
    provider = get_llm_provider()
    if autogen_openai is None:
        raise ImportError(
            "autogen-ext is not installed. Install 'autogen-ext[openai]' or provide "
            "MAGENTIC_MODEL_CLIENT_FACTORY to supply a custom client."
        )

    match provider:
        case Provider.AZURE:
            azure_deployment = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]
            azure_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
            api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
            return AzureOpenAIChatCompletionClient(
                azure_deployment=azure_deployment,
                model=azure_deployment,
                api_version=api_version,
                azure_endpoint=azure_endpoint,
                azure_ad_token_provider=app_context.cognitive_services_token_provider,
            )
        case Provider.LOCAL:
            # fetch model id
            model_id = (
                os.getenv("LOCAL_LLM_MODEL_ID")
                or os.getenv("LLM_MODEL_ID")
                or os.getenv("OPENAI_MODEL_NAME")
                or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
            )
            if model_id is None:
                raise EnvironmentError("LOCAL_LLM_MODEL_ID (or LLM_MODEL_ID/OPENAI_MODEL_NAME/AZURE_OPENAI_DEPLOYMENT_NAME) must be set when LLM_PROVIDER=local.")

            # fetch LLM endpoint url
            base_url = (
                os.getenv("LOCAL_LLM_AUTOGEN_BASE_URL")
                or os.getenv("LOCAL_LLM_BASE_URL")
                or os.getenv("LOCAL_LLM_ENDPOINT")
            )
            if base_url is not None:
                base_url = base_url.rstrip("/")
                if base_url.endswith("/chat/completions"):
                    base_url = base_url[: base_url.rfind("/chat/completions")]

            # fetch api key
            api_key = os.getenv("LOCAL_LLM_API_KEY")

            kwargs: dict[str, Any] = {}
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            return OpenAIChatCompletionClient(model=model_id, **kwargs)
        case _:
            raise NotImplementedError(f"Unsupported LLM provider '{provider.value}'.")


def _create_model_client(app_context: AppContext):
    factory_spec = os.getenv("MAGENTIC_MODEL_CLIENT_FACTORY")
    if factory_spec:
        factory = _load_factory(factory_spec)
        return factory(app_context=app_context)

    return _create_default_model_client(app_context)


def convert_tools(agent: Agent):
    tools = []
    for plugin in agent.kernel.plugins.values():
        for function in plugin.functions.values():
            tools.append(function.method)  # type: ignore
    return tools


def create_magentic_chat(chat: AgentGroupChat, app_context: AppContext, input_func) -> MagenticOneGroupChat:
    agent_config = app_context.all_agent_configs
    model_client = _create_model_client(app_context)

    assistants: list[ChatAgent] = [
        AssistantAgent(agent.name, model_client=model_client, tools=convert_tools(agent),
                       system_message=agent.instructions, description=next((
                           config["description"]
                           for config in agent_config if agent.name == config["name"]
                       ), agent.name))
        for agent in chat.agents
    ]

    user_proxy = UserProxyAgent(name="user", description="The user. As a last resort, when all else has been tried, we can ask the user for information.", input_func=input_func)
    assistants.append(user_proxy)

    team = MagenticOneGroupChat(assistants, model_client=model_client, max_turns=50)
    return team
