from .base import AIProvider, AIResult, AIError
from .mock import MockProvider


def get_provider(settings):
    """Return the configured provider without silently substituting another provider."""
    from ..config import ConfigError

    if settings.ai_provider == "openai":
        if not isinstance(settings.openai_api_key, str) or not settings.openai_api_key.strip():
            raise ConfigError("AI_PROVIDER=openai requires OPENAI_API_KEY; configure it in the local .env file")
        from .openai_provider import OpenAIProvider
        return OpenAIProvider(settings.openai_api_key, settings.openai_model, settings.openai_timeout), ""
    if settings.ai_provider == "mock":
        return MockProvider(), ""
    raise ConfigError("AI_PROVIDER must be 'mock' or 'openai'")
