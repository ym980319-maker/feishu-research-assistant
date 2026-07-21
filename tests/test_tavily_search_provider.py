from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.providers import public_search_provider
from app.providers.public_search_provider import (
    MockPublicSearchProvider,
    get_configured_public_search_provider,
    has_traceable_source,
    search_public_information,
)
from app.providers.tavily_search_provider import (
    TAVILY_SEARCH_URL,
    TavilySearchProvider,
)
from app.services.report_analysis_service import handle_report_analysis


class TavilySearchProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_official_http_contract_and_normalizes_result(self) -> None:
        response = MagicMock()
        response.json.return_value = {
            "results": [
                {
                    "title": "没有发布日期的结果",
                    "url": "https://first.example.com/item",
                    "content": "第一条内容",
                },
                {
                    "title": "基金公告",
                    "url": "https://www.fund.example.com/announcement",
                    "published_date": "2026-07-21",
                    "content": "基金公告正文",
                },
            ]
        }
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch(
            "app.providers.tavily_search_provider.httpx.AsyncClient",
            return_value=context,
        ):
            result = await TavilySearchProvider("tvly-secret").search("某基金")

        response.raise_for_status.assert_called_once_with()
        client.post.assert_awaited_once()
        args, kwargs = client.post.await_args
        self.assertEqual(args[0], TAVILY_SEARCH_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tvly-secret")
        self.assertEqual(
            kwargs["json"],
            {
                "query": "某基金",
                "topic": "finance",
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        self.assertEqual(result["title"], "基金公告")
        self.assertEqual(result["source"], "fund.example.com")
        self.assertEqual(result["url"], "https://www.fund.example.com/announcement")
        self.assertEqual(result["publish_time"], "2026-07-21")
        self.assertEqual(result["content"], "基金公告正文")
        self.assertTrue(has_traceable_source(result))

    async def test_search_does_not_invent_missing_publish_time(self) -> None:
        response = MagicMock()
        response.json.return_value = {
            "results": [
                {
                    "title": "无日期网页",
                    "url": "https://example.com/no-date",
                    "content": "网页内容",
                }
            ]
        }
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch(
            "app.providers.tavily_search_provider.httpx.AsyncClient",
            return_value=context,
        ):
            result = await TavilySearchProvider("tvly-secret").search("查询")

        self.assertEqual(result["source"], "example.com")
        self.assertEqual(result["publish_time"], "")
        self.assertFalse(has_traceable_source(result))

    async def test_empty_key_or_query_does_not_call_http(self) -> None:
        with patch(
            "app.providers.tavily_search_provider.httpx.AsyncClient"
        ) as client_factory:
            self.assertEqual(await TavilySearchProvider("").search("信用债"), {})
            self.assertEqual(await TavilySearchProvider("tvly-key").search("  "), {})

        client_factory.assert_not_called()

    def test_tavily_is_selected_lazily_from_environment(self) -> None:
        with patch.object(
            public_search_provider,
            "DEFAULT_PUBLIC_SEARCH_PROVIDER",
            None,
        ), patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-from-env"}):
            provider = get_configured_public_search_provider()

        self.assertIsInstance(provider, TavilySearchProvider)
        self.assertEqual(provider.api_key, "tvly-from-env")

    def test_mock_remains_default_without_api_key(self) -> None:
        with patch.object(
            public_search_provider,
            "DEFAULT_PUBLIC_SEARCH_PROVIDER",
            None,
        ), patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
            provider = get_configured_public_search_provider()

        self.assertIsInstance(provider, MockPublicSearchProvider)

    async def test_configured_provider_failure_degrades_to_empty_result(self) -> None:
        provider = AsyncMock()
        provider.search.side_effect = RuntimeError("remote failure")

        result = await search_public_information("信用债", provider=provider)

        self.assertEqual(
            result,
            {
                "title": "",
                "source": "",
                "url": "",
                "publish_time": "",
                "content": "",
            },
        )

    async def test_report_analysis_search_is_optional(self) -> None:
        public_researcher = AsyncMock()
        knowledge_provider = AsyncMock(return_value="知识库材料")
        model_handler = AsyncMock(return_value="分析结果")

        await handle_report_analysis(
            "分析这份研报",
            model_handler,
            knowledge_provider,
            public_researcher,
        )

        public_researcher.assert_not_awaited()
        model_handler.assert_awaited_once()

    async def test_report_analysis_searches_when_user_requests_update(self) -> None:
        public_researcher = AsyncMock(
            return_value={
                "subject": "补充最新公开信息",
                "announcements": [],
                "news": [],
                "regulatory_info": [],
            }
        )
        knowledge_provider = AsyncMock(return_value="知识库材料")
        model_handler = AsyncMock(return_value="分析结果")

        await handle_report_analysis(
            "分析研报并补充最新公开信息",
            model_handler,
            knowledge_provider,
            public_researcher,
        )

        public_researcher.assert_awaited_once_with("分析研报并补充最新公开信息")
        model_handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

