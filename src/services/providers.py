import os
from enum import Enum


class Provider(Enum):
    AZURE = "azure"
    LOCAL = "local"


def get_llm_provider() -> Provider:
    """Return the configured LLM provider based on environment settings."""
    default = Provider.AZURE.value
    return Provider(os.getenv("LLM_PROVIDER", default).lower())
