from __future__ import annotations

import json
import sys
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, patch


class _FastAPI:
    def __init__(self, **kwargs):
        pass

    def get(self, path):
        return lambda function: function

    def post(self, path):
        return lambda function: function


fastapi = ModuleType("fastapi")
fastapi.FastAPI = _FastAPI
fastapi.Request = object
fastapi_responses = ModuleType("fastapi.responses")
fastapi_responses.JSONResponse = lambda value: value
dotenv = ModuleType("dotenv")
dotenv.load_dotenv = lambda: None
sys.modules.setdefault("fastapi", fastapi)
sys.modules.setdefault("fastapi.responses", fastapi_responses)
sys.modules.setdefault("dotenv", dotenv)

import app.main as main


def record(**fields):
    return {"fields": fields}


class _Request:
    def __init__(self, text: str, message_id: str):
        self.body = {
            "event": {
                "message": {
                    "message_id": message_id,
                    "message_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                }
            }
        }

    async def json(self):
        return self.body


class FixedIncomeDailyReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.PROCESSING_MESSAGE_IDS.clear()
        main.PROCESSED_MESSAGE_IDS.clear()

    async def test_four_sources_enter_one_kimi_call_and_prompt_is_fixed_income(self) -> None:
        market = [record(指标名称="10年期国债收益率", 数值="1.75", 单位="%")]
        news = [record(标题="央行开展公开市场操作", 摘要="流动性保持合理充裕")]
        knowledge = [record(素材标题="货币政策框架", 核心结论="观察资金价格")]
        reports = [
            record(报告标题="昨日固收投研日报", 报告类型="深度报告", 核心结论="历史日报"),
            record(报告标题="城投债深度报告", 报告类型="深度报告", 核心结论="关注区域分化"),
        ]

        async def read_table(table_id: str, limit: int):
            return {
                "market-table": market,
                "knowledge-table": knowledge,
                "report-table": reports,
            }[table_id]

        table_reader = AsyncMock(side_effect=read_table)
        news_reader = AsyncMock(return_value=news)
        kimi = AsyncMock(return_value="唯一固收日报正文")

        with patch.object(main, "FEISHU_MARKET_TABLE_ID", "market-table"), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ), patch.object(
            main, "FEISHU_KNOWLEDGE_TABLE_ID", "knowledge-table"
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", "report-table"
        ), patch.object(
            main, "read_recent_table_records", table_reader
        ), patch.object(
            main, "read_recent_news_records", news_reader
        ), patch.object(
            main, "call_kimi", kimi
        ):
            result = await main.generate_daily_report("固收投研日报")

        self.assertEqual(result, "唯一固收日报正文")
        table_reader.assert_any_await("market-table", limit=30)
        table_reader.assert_any_await("knowledge-table", limit=10)
        table_reader.assert_any_await("report-table", limit=5)
        self.assertEqual(table_reader.await_count, 3)
        news_reader.assert_awaited_once_with(limit=10)
        kimi.assert_awaited_once()

        prompt, task_type = kimi.await_args.args
        self.assertEqual(task_type, "投研日报")
        for required in (
            "固收投研日报",
            "今日固收核心结论",
            "资金面与流动性",
            "利率债市场",
            "信用债市场",
            "可转债与跨资产表现",
            "为什么重要",
            "对固收市场的影响",
            "对缺失数据必须明确说明，不得编造或推断",
            "10年期国债收益率",
            "央行开展公开市场操作",
            "货币政策框架",
            "城投债深度报告",
        ):
            self.assertIn(required, prompt)
        self.assertNotIn("昨日固收投研日报", prompt)
        self.assertNotIn("核心结论：历史日报", prompt)

    def test_fixed_income_indicators_are_not_filtered_by_whitelist(self) -> None:
        text = main.format_market_records_for_daily(
            [
                record(指标名称="DR007", 数值="1.82", 单位="%"),
                record(指标名称="R007", 数值="1.95", 单位="%"),
                record(指标名称="10Y国债收益率", 收益率="1.75", 单位="%"),
                record(指标名称="30年期国债收益率", 收益率="2.05"),
                record(指标名称="AAA信用利差", 利差="42", 单位="BP"),
                record(指标名称="上证指数", 数值="3500", 涨跌幅="0.4"),
                record(指标名称="自定义波动指标", 最新值="17.3"),
                record(指标名称="无数值指标"),
            ]
        )

        self.assertIn("【固收与资金指标】", text)
        self.assertIn("DR007：1.82%", text)
        self.assertIn("R007：1.95%", text)
        self.assertIn("10Y国债收益率：1.75%", text)
        self.assertIn("30年期国债收益率：2.05", text)
        self.assertIn("AAA信用利差：42BP", text)
        self.assertIn("【权益市场（跨资产参考）】", text)
        self.assertIn("上证指数：3500", text)
        self.assertIn("【其他市场指标】", text)
        self.assertIn("自定义波动指标：17.3", text)
        self.assertNotIn("无数值指标", text)

    def test_historical_daily_filter_is_conservative(self) -> None:
        deep_report = record(报告标题="城投化债专题深度报告", 报告类型="深度报告")
        items = [
            record(报告标题="投研日报-20260719", 报告类型="深度报告"),
            record(报告标题="昨日市场晨报", 报告类型="研究资料"),
            record(报告标题="普通标题", 报告类型="固收投研日报"),
            deep_report,
        ]

        self.assertEqual(main.filter_historical_daily_reports(items), [deep_report])

    async def test_empty_source_still_generates_with_one_kimi_call(self) -> None:
        table_reader = AsyncMock(return_value=[])
        news_reader = AsyncMock(return_value=[])
        kimi = AsyncMock(return_value="空资料下的固收日报")

        with patch.object(main, "FEISHU_MARKET_TABLE_ID", "market-table"), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ), patch.object(
            main, "FEISHU_KNOWLEDGE_TABLE_ID", "knowledge-table"
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", "report-table"
        ), patch.object(
            main, "read_recent_table_records", table_reader
        ), patch.object(
            main, "read_recent_news_records", news_reader
        ), patch.object(
            main, "call_kimi", kimi
        ):
            result = await main.generate_daily_report("今日固收日报")

        self.assertEqual(result, "空资料下的固收日报")
        kimi.assert_awaited_once()
        self.assertIn("任何一类资料为空时仍按九个板块生成", kimi.await_args.args[0])

    async def test_kimi_failure_returns_one_structured_fallback_without_retry(self) -> None:
        kimi = AsyncMock(return_value="Kimi 调用失败：timeout")
        with patch.object(main, "FEISHU_MARKET_TABLE_ID", None), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", None
        ), patch.object(
            main, "FEISHU_KNOWLEDGE_TABLE_ID", None
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", None
        ), patch.object(
            main, "call_kimi", kimi
        ):
            result = await main.generate_daily_report("生成固收日报")

        kimi.assert_awaited_once()
        self.assertEqual(result.count("【固收投研日报（简版）】"), 1)
        self.assertEqual(result.count("一、今日固收核心结论"), 1)
        self.assertEqual(result.count("九、今日关注与风险提示"), 1)
        self.assertIn("暂无新增资金面量化数据", result)
        self.assertNotIn("Kimi 调用失败", result)

    async def test_archive_writes_document_and_report_once(self) -> None:
        document = AsyncMock(return_value="https://example.com/fixed-income-daily")
        report_writer = AsyncMock()
        with patch.object(
            main, "generate_daily_report", AsyncMock(return_value="唯一日报正文")
        ), patch.object(
            main, "create_feishu_doc", document
        ), patch.object(
            main, "write_report_record", report_writer
        ):
            result = await main.handle_daily_report("固收投研日报")

        document.assert_awaited_once()
        title, content = document.await_args.args
        self.assertTrue(title.startswith("固收投研日报-"))
        self.assertEqual(content, "唯一日报正文")
        report_writer.assert_awaited_once_with(
            "固收投研日报",
            "唯一日报正文",
            "https://example.com/fixed-income-daily",
        )
        self.assertEqual(result.count("唯一日报正文"), 1)

    async def test_existing_and_new_daily_commands_are_recognized(self) -> None:
        commands = (
            "投研日报",
            "生成投研日报",
            "生成日报",
            "今日投研日报",
            "固收投研日报",
            "生成固收日报",
            "今日固收日报",
        )
        for index, command in enumerate(commands):
            with self.subTest(command=command):
                daily = AsyncMock(return_value="日报正文")
                reply = AsyncMock()
                with patch.object(main, "handle_daily_report", daily), patch.object(
                    main, "write_task_record", AsyncMock()
                ), patch.object(
                    main, "reply_feishu_message", reply
                ), patch.object(
                    main, "call_deepseek", AsyncMock()
                ) as deepseek:
                    await main.feishu_events(
                        _Request(command, message_id=f"fixed-income-command-{index}")
                    )

                daily.assert_awaited_once_with(command)
                deepseek.assert_not_awaited()
                reply.assert_awaited_once_with(
                    f"fixed-income-command-{index}",
                    "日报正文",
                )


if __name__ == "__main__":
    unittest.main()
