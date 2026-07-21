"""Tavily implementation of the public-search provider contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx


TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _source_from_url(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname.removeprefix("www.")


def normalize_tavily_result(value: Mapping[str, Any]) -> dict[str, str]:
    """Map one Tavily item to the project's stable five-field contract."""
    url = _text(value.get("url"))
    publish_time = _text(
        value.get("published_date")
        or value.get("published_time")
        or value.get("publish_time")
        or value.get("published_at")
        or value.get("date")
    )
    return {
        "title": _text(value.get("title")),
        "source": _text(value.get("source")) or _source_from_url(url),
        "url": url,
        "publish_time": publish_time,
        "content": _text(value.get("content")),
    }


class TavilySearchProvider:
    """Search Tavily over HTTP without adding an SDK dependency."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = TAVILY_SEARCH_URL,
        timeout: float = 20.0,
    ):
        self.api_key = str(api_key or "").strip()
        self.endpoint = endpoint
        self.timeout = timeout

    async def search(self, query: str) -> Mapping[str, Any]:
        normalized_query = str(query or "").strip()
        if not self.api_key or not normalized_query:
            return {}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": normalized_query,
            "topic": "finance",
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": False,
            "include_raw_content": False,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint,
                headers=headers,
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", []) if isinstance(data, Mapping) else []
        if not isinstance(results, list):
            return {}

        normalized_results = [
            normalize_tavily_result(item)
            for item in results
            if isinstance(item, Mapping)
        ]
        if not normalized_results:
            return {}

        # Prefer a result with an actual publication time.  Retrieval time is
        # deliberately not substituted because it is not publication time.
        return next(
            (item for item in normalized_results if item["publish_time"]),
            normalized_results[0],
        )

