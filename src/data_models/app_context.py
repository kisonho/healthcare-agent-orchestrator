# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional

from azure.identity.aio import get_bearer_token_provider

from data_models.data_access import DataAccess


@dataclass(frozen=True)
class AppContext:
    """ Application context for commonly used objects in the application. """
    all_agent_configs: list[dict]
    blob_service_client: Any
    credential: Optional[Any]
    data_access: DataAccess

    @property
    def azureml_token_provider(self) -> Callable[[], Coroutine[Any, Any, str]]:
        if self.credential is None:
            async def _raise() -> str:  # pragma: no cover - defensive path
                raise RuntimeError("AzureML token provider is unavailable in local mode.")
            return _raise
        return get_bearer_token_provider(self.credential, "https://ml.azure.com/.default")

    @property
    def cognitive_services_token_provider(self) -> Callable[[], Coroutine[Any, Any, str]]:
        if self.credential is None:
            async def _raise() -> str:  # pragma: no cover - defensive path
                raise RuntimeError("Cognitive Services token provider is unavailable in local mode.")
            return _raise
        return get_bearer_token_provider(self.credential, "https://cognitiveservices.azure.com/.default")
