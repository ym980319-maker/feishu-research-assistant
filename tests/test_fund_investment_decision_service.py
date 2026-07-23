from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.models.evidence import Evidence
from app.services.fund_investment_decision_service import (
    FUND_PUBLIC_SEARCH_TOPICS,
    build_fund_public_queries,
    collect_fund_evidence,
    format_fund_documents,
    generate_fund_investment_decision,
)


class FundInvestmentDecisionServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_builds_four_required_public_search_queries(self) -> None:
        queries = build_fund_public_queries("示例基金")

        self.assertEqual(len(queries), 4)
        self.assertEqual(
            queries,
            tuple(f"示例基金 {topic}" for topic in FUND_PUBLIC_SEARCH_TOPICS),
        )
        self.assertIn("示例基金 基金经理公开信息", queries)
        self.assertIn("示例基金 基金管理人公告", queries)
        self.assertIn("示例基金 监管信息", queries)
        self.assertIn("示例基金 新闻舆情", queries)

    async def test_all_public_results_are_converted_to_evidence(self) -> None:
        queries = []

        async def researcher(query: str):
            queries.append(query)
            return {
                "title": query,
                "content": "公开资料正文",
                "source": "公开来源",
                "publish_time": "2026-07-21",
                "url": f"https://example.com/{len(queries)}",
            }

        evidence_pool = await collect_fund_evidence("示例基金", researcher)

        self.assertEqual(queries, list(build_fund_public_queries("示例基金")))
        self.assertEqual(len(evidence_pool), 4)
        self.assertTrue(all(isinstance(item, Evidence) for item in evidence_pool))
        self.assertTrue(all(item.source == "公开来源" for item in evidence_pool))

    async def test_missing_source_is_filtered_before_model(self) -> None:
        researcher = AsyncMock(
            return_value={
                "title": "无来源基金消息",
                "content": "不得进入模型",
                "source": "",
                "publish_time": "2026-07-21",
                "url": "https://example.com/no-source",
            }
        )
        model = AsyncMock(return_value="基金投决报告")
        knowledge = AsyncMock(return_value="基金评价框架")

        await generate_fund_investment_decision(
            "示例基金",
            model,
            knowledge,
            evidence_researcher=researcher,
        )

        prompt = model.await_args.args[0]
        self.assertNotIn("无来源基金消息", prompt)
        self.assertIn("未检索到公开资料", prompt)

    async def test_generates_one_complete_fund_decision_prompt(self) -> None:
        call_order = []

        async def researcher(query: str):
            call_order.append("public")
            return {
                "title": f"{query}结果",
                "content": "可核验公开内容",
                "source": "基金管理人官网",
                "publish_time": "2026-07-21",
                "url": "https://example.com/fund",
            }

        async def knowledge_provider(*, limit: int, user_text: str):
            call_order.append("knowledge")
            self.assertEqual(limit, 10)
            self.assertEqual(user_text, "示例基金")
            return "内部基金研究框架"

        async def kimi(prompt: str, task_type: str):
            call_order.append("kimi")
            self.assertEqual(task_type, "基金产品研究")
            return "唯一基金投决报告"

        result = await generate_fund_investment_decision(
            "示例基金",
            kimi,
            knowledge_provider,
            documents=["基金合同摘要", "定期报告摘要"],
            evidence_researcher=researcher,
        )

        self.assertEqual(
            call_order,
            ["public", "public", "public", "public", "knowledge", "kimi"],
        )
        self.assertEqual(result, "唯一基金投决报告")

    async def test_prompt_contains_required_sections_materials_and_constraints(self) -> None:
        researcher = AsyncMock(
            return_value={
                "title": "基金经理公告",
                "content": "基金经理履历公开内容",
                "source": "基金管理人官网",
                "publish_time": "2026-07-21",
                "url": "https://example.com/manager",
            }
        )
        model = AsyncMock(return_value="基金投决报告")
        knowledge = AsyncMock(return_value="内部基金评价框架")

        result = await generate_fund_investment_decision(
            "示例基金",
            model,
            knowledge,
            documents="基金合同与募集说明书摘要",
            evidence_researcher=researcher,
        )

        self.assertEqual(result, "基金投决报告")
        model.assert_awaited_once()
        prompt, task_type = model.await_args.args
        self.assertEqual(task_type, "基金产品研究")
        for section in (
            "# 产品尽调分析报告",
            "## 一、产品概况",
            "## 二、投资策略与收益来源分析",
            "## 三、历史业绩分析",
            "## 四、风险分析",
            "## 五、组合配置价值分析",
            "## 六、投资结论",
        ):
            self.assertIn(section, prompt)
        for required in (
            "基金合同与募集说明书摘要",
            "内部基金评价框架",
            "基金经理公告",
            "基金管理人官网",
            "https://example.com/manager",
            "不允许编造基金规模",
            "无来源内容不得作为事实输出",
            "XX%",
            "X亿元",
            "Xbp",
            "XX公司",
            "材料未披露",
            "产品主要收益来源",
            "### 需要进一步尽调的问题",
        ):
            self.assertIn(required, prompt)

    async def test_empty_public_search_still_generates_report(self) -> None:
        researcher = AsyncMock(return_value={})
        model = AsyncMock(return_value="缺资料基金投决报告")
        knowledge = AsyncMock(return_value="")

        result = await generate_fund_investment_decision(
            "示例基金",
            model,
            knowledge,
            evidence_researcher=researcher,
        )

        self.assertEqual(result, "缺资料基金投决报告")
        self.assertEqual(researcher.await_count, 4)
        prompt = model.await_args.args[0]
        self.assertIn("未检索到公开资料", prompt)
        self.assertIn("公开资料未找到", prompt)
        self.assertIn("未提供基金合同、募集说明书或定期报告", prompt)
        self.assertIn("暂无相关知识库材料", prompt)

    async def test_kimi_timeout_is_retried_once_then_succeeds(self) -> None:
        call_count = 0

        async def model(prompt: str, task_type: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(1)
            return "重试后生成的基金尽调报告"

        with patch(
            "app.services.fund_investment_decision_service."
            "FUND_KIMI_TIMEOUT_SECONDS",
            0.01,
        ):
            result = await generate_fund_investment_decision(
                "示例基金",
                model,
                AsyncMock(return_value=""),
                evidence_researcher=AsyncMock(return_value={}),
            )

        self.assertEqual(result, "重试后生成的基金尽调报告")
        self.assertEqual(call_count, 2)

    async def test_repeated_kimi_timeout_returns_friendly_message(self) -> None:
        model = AsyncMock(
            side_effect=[
                "调用 Kimi 超时，请稍后重试",
                "调用 Kimi 超时，请稍后重试",
            ]
        )

        result = await generate_fund_investment_decision(
            "示例基金",
            model,
            AsyncMock(return_value=""),
            evidence_researcher=AsyncMock(return_value={}),
        )

        self.assertEqual(model.await_count, 2)
        self.assertIn("已自动重试一次", result)
        self.assertIn("请稍后重新提交", result)

    async def test_kimi_prompt_receives_document_body_and_logs_length(self) -> None:
        documents = "PDF完整正文：投资范围包括利率债和高等级信用债。"
        model = AsyncMock(return_value="基金尽调报告")

        with patch("builtins.print") as print_log:
            await generate_fund_investment_decision(
                "示例基金",
                model,
                AsyncMock(return_value=""),
                documents=documents,
                evidence_researcher=AsyncMock(return_value={}),
            )

        prompt = model.await_args.args[0]
        formatted_documents = format_fund_documents(documents)
        self.assertIn(documents, prompt)
        messages = [
            " ".join(str(value) for value in call.args)
            for call in print_log.call_args_list
        ]
        self.assertTrue(
            any(
                f"Kimi收到的正文长度: {len(formatted_documents)}" in message
                for message in messages
            )
        )

    def test_optional_documents_are_formatted_without_fabrication(self) -> None:
        self.assertEqual(
            format_fund_documents(None),
            "未提供基金合同、募集说明书或定期报告。",
        )
        formatted = format_fund_documents(["基金合同", "定期报告"])
        self.assertIn("【基金材料 1】\n基金合同", formatted)
        self.assertIn("【基金材料 2】\n定期报告", formatted)


if __name__ == "__main__":
    unittest.main()
