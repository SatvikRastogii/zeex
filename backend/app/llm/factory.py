import logging
from functools import lru_cache

from app.config import get_settings
from app.llm.gemini import GeminiProvider
from app.llm.mock import get_mock
from app.llm.provider import LLMProvider

log = logging.getLogger("llm")


@lru_cache
def _gemini() -> GeminiProvider | None:
    s = get_settings()
    try:
        return GeminiProvider(s.gemini_api_key, s.gemini_parse_model, s.gemini_write_model)
    except ValueError as e:
        log.error("LLM_PROVIDER=gemini but %s; using the mock provider", e)
        return None


def get_provider() -> LLMProvider:
    """Gemini when configured, otherwise the (clearly labelled) mock."""
    if get_settings().llm_provider == "gemini":
        g = _gemini()
        if g is not None:
            return g
    return get_mock()


def provider_name() -> str:
    return get_provider().name
