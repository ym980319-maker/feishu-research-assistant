from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.models.evidence import Evidence
from app.services import evidence_service
from app.services.evidence_service import (
    build_evidence_research_prompt,
    collect_evidence_materials,
    collect_public_evidence,
    format_evidence_pool,
)


class EvidenceServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_evidence_has_stable_required_fields(self) -> None:
        evidence = Evidence(
            title="监管公告",
            content="公告正文",
            source="监管机构",
            published_time="2026-07-21",
            url="https://example.com/regulation",
        )

        self.assertEqual(evidence.title, "监管公告")
        self.assertEqual(evidence.content, "公告正文")
        self.assertEqual(evidence.source, "监管机构")
        self.assertEqual(evidence.published_time, "2026-07-21")
        self.assertEqual(evidence.url, "https://example.com/regulation")

    async def test_search_results_are_converted_filtered_and_prioritized(self) -> None:
        async def researcher(query: str):
            self.assertEqual(query, "信用债")
            return {
                "subject": query,
                "announcements": [
                    {
                        "title": "缺少来源的内容",
                        "content": "不得进入模型",
                        "publish_time": "2026-07-21",
                    },
                    {
                        "title": "有来源但缺少时间",
                        "content": "低优先级线索",
                        "source": "行业协会",
                        "url": "https://example.com/no-time",
                    },
                ],
                "news": [
                    {
                        "title": "有完整时间的新闻",
                        "content": "公开新闻内容",
                        "source": "新闻机构",
                        "publish_time": "2026-07-21 09:00",
                        "url": "https://example.com/news",
                    }
                ],
                "regulatory_info": [],
            }

        evidence_pool = await collect_public_evidence("信用债", researcher)

        self.assertTrue(all(isinstance(item, Evidence) for item in evidence_pool))
        self.assertEqual(
            [item.title for item in evidence_pool],
            ["有完整时间的新闻", "有来源但缺少时间"],
        )
        self.assertEqual(evidence_pool[0].published_time, "2026-07-21 09:00")
        self.assertEqual(evidence_pool[1].published_time, "")
        self.assertEqual(evidence_pool[1].url, "https://example.com/no-time")
        self.assertNotIn("缺少来源的内容", [item.title for item in evidence_pool])

    async def test_default_path_calls_existing_public_search_interface(self) -> None:
        search = AsyncMock(
            return_value={
                "title": "基金公告",
                "content": "基金公告正文",
                "source": "基金管理人官网",
                "publish_time": "2026-07-21",
                "url": "https://example.com/fund",
            }
        )
        with patch.object(evidence_service, "search_public_information", search):
            evidence_pool = await collect_public_evidence("某基金")

        search.assert_awaited_once_with("某基金")
        self.assertEqual(len(evidence_pool), 1)
        self.assertIsInstance(evidence_pool[0], Evidence)
        self.assertEqual(evidence_pool[0].source, "基金管理人官网")

    async def test_duplicate_evidence_is_removed(self) -> None:
        item = {
            "title": "重复公告",
            "content": "公告正文",
            "source": "公司官网",
            "publish_time": "2026-07-21",
            "url": "https://example.com/announcement",
        }
        evidence_pool = await collect_public_evidence(
            "公司",
            AsyncMock(return_value=[item, dict(item)]),
        )

        self.assertEqual(len(evidence_pool), 1)

    async def test_search_failure_returns_empty_evidence_pool(self) -> None:
        researcher = AsyncMock(side_effect=RuntimeError("search unavailable"))

        evidence_pool = await collect_public_evidence("城投债", researcher)

        self.assertEqual(evidence_pool, [])

    async def test_collect_materials_keeps_public_then_knowledge_order(self) -> None:
        calls = []

        async def researcher(query: str):
            calls.append("public")
            return {
                "title": "公开信息",
                "content": "公开内容",
                "source": "公开来源",
                "publish_time": "2026-07-21",
                "url": "https://example.com/public",
            }

        async def knowledge_provider(*, limit: int, user_text: str):
            calls.append("knowledge")
            self.assertEqual(limit, 10)
            return "知识库材料"

        evidence_pool, knowledge = await collect_evidence_materials(
            "研究主题",
            knowledge_provider,
            researcher,
        )

        self.assertEqual(calls, ["public", "knowledge"])
        self.assertEqual(len(evidence_pool), 1)
        self.assertEqual(knowledge, "知识库材料")

    def test_empty_pool_has_explicit_message_and_anti_fabrication_rules(self) -> None:
        text = format_evidence_pool([])

        self.assertIn("未检索到公开资料", text)
        self.assertIn("基于以下公开信息生成，不允许编造事实", text)
        self.assertIn("XX%", text)
        self.assertIn("X亿元", text)
        self.assertIn("Xbp", text)
        self.assertIn("XX公司", text)

    def test_prompt_keeps_source_time_and_url(self) -> None:
        prompt = build_evidence_research_prompt(
            "分析基金",
            [
                Evidence(
                    title="基金公告",
                    content="公告内容",
                    source="基金管理人官网",
                    published_time="2026-07-21",
                    url="https://example.com/fund",
                )
            ],
            "基金评价框架",
        )

        self.assertIn("【公开信息证据池】", prompt)
        self.assertIn("基金管理人官网", prompt)
        self.assertIn("2026-07-21", prompt)
        self.assertIn("https://example.com/fund", prompt)
        self.assertIn("基金评价框架", prompt)


if __name__ == "__main__":
    unittest.main()

