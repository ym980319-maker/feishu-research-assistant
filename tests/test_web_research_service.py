from __future__ import annotations

import unittest

from app.services.fund_analysis_service import handle_fund_analysis
from app.services.report_analysis_service import handle_report_analysis
from app.services.research_report_service import handle_research_report
from app.services.web_research_service import (
    MockPublicInfoProvider,
    research_public_info,
)


class _FailingProvider:
    async def research(self, subject: str):
        raise RuntimeError("provider unavailable")


class WebResearchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_mock_provider_returns_stable_empty_structure(self) -> None:
        result = await research_public_info("城投债")

        self.assertEqual(
            result,
            {
                "subject": "城投债",
                "announcements": [],
                "news": [],
                "regulatory_info": [],
            },
        )

    async def test_mock_provider_can_be_replaced_with_structured_results(self) -> None:
        provider = MockPublicInfoProvider(
            {
                "某基金": {
                    "announcements": [
                        {
                            "title": "基金经理变更公告",
                            "source": "基金管理人",
                            "publish_time": "2026-07-21",
                        }
                    ],
                    "news": [
                        {
                            "title": "产品规模变化",
                            "source": "公开新闻",
                            "publish_time": "2026-07-21",
                        }
                    ],
                    "regulatory_info": [
                        {
                            "title": "监管规则",
                            "source": "监管机构",
                            "publish_time": "2026-07-21",
                        }
                    ],
                }
            }
        )

        result = await research_public_info("某基金", provider=provider)

        self.assertEqual(result["subject"], "某基金")
        self.assertEqual(result["announcements"][0]["title"], "基金经理变更公告")
        self.assertEqual(result["news"][0]["title"], "产品规模变化")
        self.assertEqual(result["regulatory_info"][0]["title"], "监管规则")

    async def test_provider_failure_degrades_to_empty_structure(self) -> None:
        result = await research_public_info("信用债", provider=_FailingProvider())

        self.assertEqual(result["subject"], "信用债")
        self.assertEqual(result["announcements"], [])
        self.assertEqual(result["news"], [])
        self.assertEqual(result["regulatory_info"], [])

    async def test_report_analysis_collects_public_and_knowledge_before_kimi(self) -> None:
        calls = []

        async def public_researcher(subject: str):
            calls.append("public")
            return {
                "subject": subject,
                "announcements": [
                    {
                        "title": "公司公告",
                        "source": "上市公司",
                        "publish_time": "2026-07-21",
                    }
                ],
                "news": [],
                "regulatory_info": [],
            }

        async def knowledge_provider(*, limit: int, user_text: str):
            calls.append("knowledge")
            self.assertEqual(limit, 10)
            self.assertEqual(user_text, "分析这份研报并补充最新公开信息")
            return "知识库中的历史研究框架"

        async def kimi(prompt: str, task_type: str):
            calls.append("kimi")
            self.assertEqual(task_type, "研报摘要")
            self.assertIn("公司公告", prompt)
            self.assertIn("知识库中的历史研究框架", prompt)
            return "研报分析结果"

        result = await handle_report_analysis(
            "分析这份研报并补充最新公开信息",
            kimi,
            knowledge_provider,
            public_researcher,
        )

        self.assertEqual(calls, ["public", "knowledge", "kimi"])
        self.assertEqual(result, "研报分析结果")

    async def test_fund_analysis_merges_materials_before_kimi(self) -> None:
        calls = []

        async def public_researcher(subject: str):
            calls.append("public")
            return {
                "subject": subject,
                "announcements": [],
                "news": [
                    {
                        "title": "基金产品公开信息",
                        "source": "基金管理人",
                        "publish_time": "2026-07-21",
                    }
                ],
                "regulatory_info": [],
            }

        async def knowledge_provider(*, limit: int, user_text: str):
            calls.append("knowledge")
            return "基金评价框架"

        async def kimi(prompt: str, task_type: str):
            calls.append("kimi")
            self.assertEqual(task_type, "基金产品研究")
            self.assertIn("基金产品公开信息", prompt)
            self.assertIn("基金评价框架", prompt)
            return "基金分析结果"

        result = await handle_fund_analysis(
            "分析这个基金",
            kimi,
            knowledge_provider,
            public_researcher,
        )

        self.assertEqual(calls, ["public", "knowledge", "kimi"])
        self.assertEqual(result, "基金分析结果")

    async def test_research_report_passes_collected_context_to_generator(self) -> None:
        calls = []

        async def public_researcher(subject: str):
            calls.append("public")
            return {
                "subject": subject,
                "announcements": [],
                "news": [],
                "regulatory_info": [
                    {
                        "title": "监管公开信息",
                        "source": "监管机构",
                        "publish_time": "2026-07-21",
                    }
                ],
            }

        async def knowledge_provider(*, limit: int, user_text: str):
            calls.append("knowledge")
            return "信用债知识库材料"

        async def report_generator(
            message: str,
            task_type: str,
            knowledge_text: str | None,
            public_info_text: str,
        ):
            calls.append("kimi")
            self.assertEqual(message, "写一篇信用债专题报告")
            self.assertEqual(task_type, "深度报告")
            self.assertEqual(knowledge_text, "信用债知识库材料")
            self.assertIn("监管公开信息", public_info_text)
            return "深度报告结果"

        result = await handle_research_report(
            "写一篇信用债专题报告",
            report_generator,
            knowledge_provider,
            public_researcher,
        )

        self.assertEqual(calls, ["public", "knowledge", "kimi"])
        self.assertEqual(result, "深度报告结果")


if __name__ == "__main__":
    unittest.main()
