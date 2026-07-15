from __future__ import annotations

import sys
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch


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


def news_record(
    title: str,
    *,
    topic: str = "动力电池",
    summary: str = "行业事件摘要",
    subject: str = "宁德时代",
) -> dict:
    return {
        "标题": title,
        "主题": topic,
        "摘要": summary,
        "公司/主体": subject,
        "情绪方向": "中性",
        "影响程度": "中等",
        "日期": "2026-07-15",
    }


class DeepReportNewsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_feature_off_does_not_query_news(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        query = AsyncMock()
        with patch(
            "app.providers.registry.official_research_enabled", return_value=False
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(
            main, "query_subject_news_records", query
        ), patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ):
            result = await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        query.assert_not_awaited()
        kimi.assert_awaited_once_with("深度报告 宁德时代", "深度报告")
        self.assertEqual(result, "报告正文")

    async def test_issuer_query_only_includes_relevant_news(self) -> None:
        query = AsyncMock(
            return_value=[
                news_record("宁德时代发布新产品"),
                news_record("无关公司新闻", subject="其他公司", summary="其他行业事件"),
            ]
        )
        with patch.object(main, "query_subject_news_records", query), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ):
            output = await main.read_subject_news_for_deep_report(
                {"issuer": "宁德时代", "stock_code": None},
                limit=5,
            )

        query.assert_awaited_once_with("宁德时代", limit=20)
        self.assertIn("宁德时代发布新产品", output)
        self.assertNotIn("无关公司新闻", output)

    async def test_stock_code_match_is_supported(self) -> None:
        query = AsyncMock(
            return_value=[news_record("公司公告线索", subject="宁德时代 300750")]
        )
        with patch.object(main, "query_subject_news_records", query), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ):
            output = await main.read_subject_news_for_deep_report(
                {"issuer": None, "stock_code": "300750"},
                limit=5,
            )

        query.assert_awaited_once_with("300750", limit=20)
        self.assertIn("公司公告线索", output)

    async def test_server_query_uses_subject_filter(self) -> None:
        response = MagicMock()
        response.json.return_value = {
            "code": 0,
            "data": {"items": [{"fields": news_record("宁德时代新闻")}]},
        }
        client = AsyncMock()
        client.post.return_value = response
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
            main, "get_tenant_access_token", AsyncMock(return_value="token")
        ), patch.object(
            main.httpx, "AsyncClient", return_value=context
        ), patch.object(
            main, "FEISHU_BITABLE_APP_TOKEN", "app-token"
        ), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ):
            records = await main.query_subject_news_records("宁德时代", limit=20)

        self.assertEqual(len(records), 1)
        args, kwargs = client.post.await_args
        self.assertTrue(args[0].endswith("/news-table/records/search"))
        self.assertEqual(kwargs["params"], {"page_size": 20})
        self.assertEqual(kwargs["json"]["filter"]["conjunction"], "or")
        conditions = kwargs["json"]["filter"]["conditions"]
        self.assertEqual(
            {condition["field_name"] for condition in conditions},
            {"标题", "主题", "摘要", "公司/主体"},
        )
        self.assertTrue(all(condition["value"] == ["宁德时代"] for condition in conditions))

    async def test_empty_subject_does_not_query_news(self) -> None:
        query = AsyncMock()
        with patch.object(main, "query_subject_news_records", query), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ):
            output = await main.read_subject_news_for_deep_report(
                {"issuer": None, "stock_code": None},
                limit=5,
            )

        query.assert_not_awaited()
        self.assertEqual(output, "")

    async def test_mixed_records_are_filtered_again_in_code(self) -> None:
        query = AsyncMock(
            return_value=[
                news_record("行业新闻", topic="宁德时代供应链"),
                news_record(
                    "海外宏观新闻",
                    topic="海外市场",
                    summary="汇率变化",
                    subject="宏观",
                ),
            ]
        )
        with patch.object(main, "query_subject_news_records", query), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ):
            output = await main.read_subject_news_for_deep_report(
                {"issuer": "宁德时代", "stock_code": None},
                limit=5,
            )

        self.assertIn("行业新闻", output)
        self.assertNotIn("海外宏观新闻", output)

    async def test_query_exception_still_calls_kimi(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.extract_research_subject",
            return_value={"issuer": "宁德时代", "stock_code": None, "query": "宁德时代"},
        ), patch(
            "app.providers.collect_official_evidence", AsyncMock(return_value=[])
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(
            main,
            "query_subject_news_records",
            AsyncMock(side_effect=RuntimeError("news unavailable")),
        ), patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", None
        ):
            result = await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        kimi.assert_awaited_once()
        self.assertNotIn("【舆情池参考资料】", kimi.await_args.args[0])
        self.assertEqual(result, "报告正文")

    async def test_no_matching_news_omits_prompt_section(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.extract_research_subject",
            return_value={"issuer": "宁德时代", "stock_code": None, "query": "宁德时代"},
        ), patch(
            "app.providers.collect_official_evidence", AsyncMock(return_value=[])
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(
            main,
            "query_subject_news_records",
            AsyncMock(return_value=[news_record("无关新闻", subject="其他公司")]),
        ), patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", None
        ):
            result = await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        self.assertNotIn("【舆情池参考资料】", kimi.await_args.args[0])
        self.assertEqual(result, "报告正文")

    async def test_at_most_five_news_items_are_formatted(self) -> None:
        records = [news_record(f"宁德时代新闻 {index}") for index in range(1, 8)]
        with patch.object(
            main, "query_subject_news_records", AsyncMock(return_value=records)
        ), patch.object(main, "FEISHU_NEWS_TABLE_ID", "news-table"):
            output = await main.read_subject_news_for_deep_report(
                {"issuer": "宁德时代", "stock_code": None},
                limit=10,
            )

        self.assertEqual(output.count("【相关舆情 "), 5)
        self.assertNotIn("宁德时代新闻 6", output)

    async def test_enabled_prompt_contains_news_rules(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.extract_research_subject",
            return_value={"issuer": "宁德时代", "stock_code": None, "query": "宁德时代"},
        ), patch(
            "app.providers.collect_official_evidence", AsyncMock(return_value=[])
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(
            main,
            "query_subject_news_records",
            AsyncMock(return_value=[news_record("宁德时代产业动态")]),
        ), patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", None
        ):
            await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        prompt = kimi.await_args.args[0]
        self.assertIn("【舆情池参考资料】", prompt)
        self.assertIn("官方资料优先级高于舆情池", prompt)
        self.assertIn("未经官方资料确认的内容不得写成确定事实", prompt)
        self.assertIn("冲突时以官方资料为准", prompt)

    async def test_does_not_use_daily_or_existing_news_functions(self) -> None:
        existing_news = AsyncMock()
        existing_query = AsyncMock()
        daily = AsyncMock()
        handle_daily = AsyncMock()
        with patch.object(
            main,
            "query_subject_news_records",
            AsyncMock(return_value=[news_record("宁德时代新闻")]),
        ), patch.object(
            main, "read_recent_news_records", existing_news
        ), patch.object(
            main, "query_bitable_records", existing_query
        ), patch.object(
            main, "generate_daily_report", daily
        ), patch.object(
            main, "handle_daily_report", handle_daily
        ), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ):
            await main.read_subject_news_for_deep_report(
                {"issuer": "宁德时代", "stock_code": None},
                limit=5,
            )

        existing_news.assert_not_awaited()
        existing_query.assert_not_awaited()
        daily.assert_not_awaited()
        handle_daily.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
