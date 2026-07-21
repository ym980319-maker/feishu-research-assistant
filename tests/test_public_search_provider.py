from __future__ import annotations

import unittest
from unittest.mock import patch

from app.providers import public_search_provider
from app.providers.public_search_provider import (
    MockPublicSearchProvider,
    has_traceable_source,
    search_public_information,
)
from app.services.web_research_service import (
    MockPublicInfoProvider,
    PublicSearchInfoProvider,
    build_research_prompt,
    research_public_info,
)


class _FailingSearchProvider:
    async def search(self, query: str):
        raise RuntimeError("search unavailable")


class PublicSearchProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_mock_returns_exact_empty_schema(self) -> None:
        result = await search_public_information("城投债")

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

    async def test_mock_provider_returns_normalized_structured_result(self) -> None:
        provider = MockPublicSearchProvider(
            {
                "某基金": {
                    "title": "基金经理变更公告",
                    "source": "基金管理人官网",
                    "url": "https://example.com/announcement",
                    "publish_time": "2026-07-21",
                    "content": "基金经理发生变更。",
                    "ignored": "不会进入统一结果",
                }
            }
        )

        result = await search_public_information("某基金", provider=provider)

        self.assertEqual(
            set(result),
            {"title", "source", "url", "publish_time", "content"},
        )
        self.assertEqual(result["title"], "基金经理变更公告")
        self.assertEqual(result["source"], "基金管理人官网")
        self.assertEqual(result["publish_time"], "2026-07-21")
        self.assertNotIn("ignored", result)
        self.assertTrue(has_traceable_source(result))

    async def test_provider_failure_returns_empty_schema(self) -> None:
        result = await search_public_information(
            "信用债",
            provider=_FailingSearchProvider(),
        )

        self.assertEqual(result["title"], "")
        self.assertEqual(result["source"], "")
        self.assertEqual(result["publish_time"], "")
        self.assertFalse(has_traceable_source(result))

    async def test_public_research_layer_calls_search_provider(self) -> None:
        provider = MockPublicSearchProvider(
            {
                "信用债": {
                    "title": "信用债监管信息",
                    "source": "监管机构官网",
                    "url": "https://example.com/regulation",
                    "publish_time": "2026-07-21",
                    "content": "公开监管内容。",
                }
            }
        )

        with patch.object(
            public_search_provider,
            "DEFAULT_PUBLIC_SEARCH_PROVIDER",
            provider,
        ):
            result = await research_public_info(
                "信用债",
                provider=PublicSearchInfoProvider(),
            )

        self.assertEqual(result["subject"], "信用债")
        self.assertEqual(len(result["news"]), 1)
        self.assertEqual(result["news"][0]["source"], "监管机构官网")
        self.assertEqual(result["news"][0]["publish_time"], "2026-07-21")

    async def test_missing_source_or_time_is_not_available_to_model(self) -> None:
        provider = MockPublicInfoProvider(
            {
                "城投债": {
                    "announcements": [
                        {
                            "title": "缺少来源的公告",
                            "publish_time": "2026-07-21",
                        },
                        {
                            "title": "缺少时间的新闻",
                            "source": "新闻机构",
                        },
                    ],
                    "news": [],
                    "regulatory_info": [],
                }
            }
        )

        public_info = await research_public_info("城投债", provider=provider)
        prompt = build_research_prompt("研究城投债", public_info, "知识库材料")

        self.assertEqual(public_info["announcements"], [])
        self.assertNotIn("缺少来源的公告", prompt)
        self.assertNotIn("缺少时间的新闻", prompt)
        self.assertIn("无来源的信息不得作为事实输出", prompt)

    async def test_traceable_information_keeps_source_and_time_in_prompt(self) -> None:
        provider = MockPublicInfoProvider(
            {
                "可转债": {
                    "announcements": [],
                    "news": [
                        {
                            "title": "可转债公开新闻",
                            "source": "公开新闻机构",
                            "url": "https://example.com/news",
                            "publish_time": "2026-07-21 09:00",
                            "content": "公开新闻内容。",
                        }
                    ],
                    "regulatory_info": [],
                }
            }
        )

        public_info = await research_public_info("可转债", provider=provider)
        prompt = build_research_prompt("研究可转债", public_info, "")

        self.assertIn("可转债公开新闻", prompt)
        self.assertIn("公开新闻机构", prompt)
        self.assertIn("2026-07-21 09:00", prompt)


if __name__ == "__main__":
    unittest.main()

