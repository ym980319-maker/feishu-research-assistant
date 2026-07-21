"""Replaceable provider interface for public-information search.

PR19 intentionally ships with an in-memory mock only.  A real search backend
can later implement ``PublicSearchProvider`` without changing research
services or model configuration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.config import TavilyConfig, load_config


PUBLIC_SEARCH_FIELDS = (
    "title",
    "source",
    "url",
    "publish_time",
    "content",
)


class PublicSearchProvider(Protocol):
    async def search(self, query: str) -> Mapping[str, Any]: ...


class MockPublicSearchProvider:
    """Return configured in-memory search results without network access."""

    def __init__(self, results: Mapping[str, Mapping[str, Any]] | None = None):
        self._results = dict(results or {})

    async def search(self, query: str) -> Mapping[str, Any]:
        return self._results.get(query, {})


DEFAULT_PUBLIC_SEARCH_PROVIDER: PublicSearchProvider | None = None


def get_configured_public_search_provider(
    config: TavilyConfig | None = None,
) -> PublicSearchProvider:
    """Resolve the provider lazily so ``load_dotenv`` has already run."""
    if DEFAULT_PUBLIC_SEARCH_PROVIDER is not None:
        return DEFAULT_PUBLIC_SEARCH_PROVIDER

    selected_config = config or load_config(load_dotenv_file=False).tavily
    if selected_config.api_key:
        from app.providers.tavily_search_provider import TavilySearchProvider

        return TavilySearchProvider(
            api_key=selected_config.api_key,
            endpoint=selected_config.endpoint,
            timeout=selected_config.timeout_seconds,
        )
    return MockPublicSearchProvider()


def empty_public_search_result() -> dict[str, str]:
    return {field: "" for field in PUBLIC_SEARCH_FIELDS}


def normalize_public_search_result(
    value: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Return exactly the stable public-search result fields."""
    result = empty_public_search_result()
    if not isinstance(value, Mapping):
        return result
    for field in PUBLIC_SEARCH_FIELDS:
        raw_value = value.get(field)
        if raw_value is not None:
            result[field] = str(raw_value).strip()
    return result


def has_traceable_source(result: Mapping[str, Any]) -> bool:
    """External information is factual input only with source and time."""
    return bool(
        str(result.get("source") or "").strip()
        and str(result.get("publish_time") or "").strip()
    )


async def search_public_information(
    query: str,
    provider: PublicSearchProvider | None = None,
) -> dict[str, str]:
    """Search through an injected provider or the environment configuration."""
    normalized_query = str(query or "").strip()
    selected_provider = provider or get_configured_public_search_provider()
    try:
        value = await selected_provider.search(normalized_query)
    except Exception as exc:
        print("公开信息搜索 Provider 调用失败，返回空结果:", type(exc).__name__)
        value = None
    return normalize_public_search_result(value)
