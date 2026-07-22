from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from app.models.evidence import Evidence
from app.models.research_task import ResearchTaskType
from app.router.task_router import (
    DAILY_REPORT,
    FUND_ANALYSIS,
    GENERAL_CHAT,
    REPORT_ANALYSIS,
    RESEARCH_REPORT,
    SENTIMENT_ANALYSIS,
)
from app.services.research_assistant_service import (
    handle_research_assistant,
    identify_research_task_type,
)


def _public_result(query: str) -> dict[str, str]:
    return {
        "title": f"{query}公开信息",
        "content": "可核验的公开资料正文",
        "source": "监管机构官网",
        "publish_time": "2026-07-21",
        "url": "https://example.com/evidence",
    }


class ResearchTaskTypeTests(unittest.TestCase):
    def test_supports_five_research_categories(self) -> None:
        cases = (
            ("整理最近舆情", SENTIMENT_ANALYSIS, ResearchTaskType.SENTIMENT_RESEARCH),
            ("分析某公司研报", REPORT_ANALYSIS, ResearchTaskType.COMPANY_RESEARCH),
            ("写一篇新能源行业专题", RESEARCH_REPORT, ResearchTaskType.INDUSTRY_RESEARCH),
            ("写一篇宏观经济专题", RESEARCH_REPORT, ResearchTaskType.MACRO_RESEARCH),
            ("生成基金投决意见", FUND_ANALYSIS, ResearchTaskType.FUND_RESEARCH),
        )

        for message, routed_task, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    identify_research_task_type(message, routed_task),
                    expected,
                )

    def test_evidence_exposes_prompt_facing_content_name(self) -> None:
        evidence = Evidence(
            title="公告",
            content="公告正文",
            source="交易所",
        )

        self.assertEqual(evidence.evidence_content, "公告正文")


class ResearchAssistantServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.kimi = AsyncMock(return_value="Kimi 研究结果")
        self.deepseek = AsyncMock(return_value="普通问答结果")
        self.knowledge = AsyncMock(return_value="内部知识库材料")
        self.deep_report = AsyncMock(return_value="深度研究结果")
        self.researcher = AsyncMock(side_effect=_public_result)

    async def _run(self, message: str, routed_task: str, **kwargs):
        return await handle_research_assistant(
            message,
            kimi_handler=self.kimi,
            deepseek_handler=self.deepseek,
            knowledge_provider=self.knowledge,
            deep_report_handler=self.deep_report,
            evidence_researcher=self.researcher,
            routed_task=routed_task,
            **kwargs,
        )

    async def test_sentiment_uses_evidence_pool_then_kimi(self) -> None:
        result = await self._run("整理最近监管舆情", SENTIMENT_ANALYSIS)

        self.assertEqual(result.content, "Kimi 研究结果")
        self.assertEqual(result.research_task_type, ResearchTaskType.SENTIMENT_RESEARCH)
        self.researcher.assert_awaited_once_with("整理最近监管舆情")
        self.kimi.assert_awaited_once()
        self.deepseek.assert_not_awaited()
        prompt, task_type = self.kimi.await_args.args
        self.assertEqual(task_type, "舆情梳理")
        for required in (
            "Evidence Pool",
            "evidence_content",
            "监管机构官网",
            "2026-07-21",
            "https://example.com/evidence",
            "无来源信息不得写成事实",
        ):
            self.assertIn(required, prompt)

    async def test_report_analysis_always_supplements_public_evidence(self) -> None:
        result = await self._run("分析这份研报", REPORT_ANALYSIS)

        self.assertEqual(result.routed_task, REPORT_ANALYSIS)
        self.researcher.assert_awaited_once_with("分析这份研报")
        self.kimi.assert_awaited_once()
        prompt = self.kimi.await_args.args[0]
        self.assertIn("监管机构官网", prompt)
        self.assertIn("evidence_content", prompt)

    async def test_deep_research_receives_evidence_before_report_generation(self) -> None:
        result = await self._run("写一篇新能源行业专题", RESEARCH_REPORT)

        self.assertEqual(result.content, "深度研究结果")
        self.assertEqual(result.research_task_type, ResearchTaskType.INDUSTRY_RESEARCH)
        self.researcher.assert_awaited_once()
        self.deep_report.assert_awaited_once()
        args = self.deep_report.await_args.args
        self.assertEqual(args[:2], ("写一篇新能源行业专题", "深度报告"))
        self.assertEqual(args[2], "内部知识库材料")
        self.assertIn("evidence_content", args[3])
        self.assertIn("监管机构官网", args[3])

    async def test_fund_decision_uses_four_evidence_queries_and_fixed_template(self) -> None:
        result = await self._run("生成示例基金投决意见", FUND_ANALYSIS)

        self.assertEqual(result.research_task_type, ResearchTaskType.FUND_RESEARCH)
        self.assertEqual(self.researcher.await_count, 4)
        self.kimi.assert_awaited_once()
        prompt = self.kimi.await_args.args[0]
        for required in (
            "# 产品尽调分析报告",
            "## 一、产品基本信息",
            "## 二、产品定位与投资逻辑",
            "## 三、投资策略拆解",
            "## 四、历史表现与风险指标",
            "## 五、资产配置与组合价值",
            "## 六、风险分析",
            "## 七、管理人与团队分析",
            "## 八、投资价值判断",
            "公开资料未找到",
            "不允许编造基金规模",
            "收益率",
            "持仓",
            "材料未披露，无法判断",
        ):
            self.assertIn(required, prompt)

    async def test_general_chat_uses_deepseek_without_public_search(self) -> None:
        result = await self._run("你好", GENERAL_CHAT)

        self.assertEqual(result.content, "普通问答结果")
        self.assertIsNone(result.research_task_type)
        self.deepseek.assert_awaited_once_with("你好", "普通问答")
        self.kimi.assert_not_awaited()
        self.researcher.assert_not_awaited()

    async def test_daily_report_remains_an_explicit_compatibility_route(self) -> None:
        daily = AsyncMock(return_value="兼容日报")

        result = await self._run(
            "投研日报",
            DAILY_REPORT,
            legacy_daily_handler=daily,
        )

        self.assertEqual(result.content, "兼容日报")
        daily.assert_awaited_once_with("投研日报")
        self.kimi.assert_not_awaited()
        self.deepseek.assert_not_awaited()
        self.researcher.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
