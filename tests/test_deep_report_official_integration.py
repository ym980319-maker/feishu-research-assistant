from __future__ import annotations

import unittest
import sys
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
from app.providers.models import Evidence


def official_evidence() -> Evidence:
    return Evidence(
        title="宁德时代关于董事会决议的公告",
        url="https://static.cninfo.com.cn/finalpage/test.pdf",
        source="巨潮资讯",
        source_type="company_announcement",
        published_at="2026-07-10T08:00:00+00:00",
        summary="",
        document_type="上市公司公告",
        issuer="宁德时代",
        stock_code="300750",
        retrieved_at="2026-07-14T08:00:00+00:00",
        verification_status="metadata_verified",
        source_priority=1,
    )


class DeepReportOfficialIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_feature_preserves_existing_knowledge_prompt(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=False
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="知识库内容")
        ), patch.object(
            main, "read_recent_news_records", AsyncMock()
        ) as read_news, patch.object(
            main, "query_bitable_records", AsyncMock()
        ) as read_history, patch.object(main, "call_kimi", kimi):
            result = await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        prompt = kimi.await_args.args[0]
        self.assertIn("【知识库参考资料】\n知识库内容", prompt)
        self.assertNotIn("官方资料：", prompt)
        self.assertNotIn("【舆情池参考资料】", prompt)
        read_news.assert_not_awaited()
        read_history.assert_not_awaited()
        self.assertEqual(
            result,
            "报告正文\n\n【系统提示】本次深度报告已参考飞书多维表格“知识库素材”中的历史资料。",
        )

    async def test_disabled_feature_without_knowledge_uses_original_user_text(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=False
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(main, "call_kimi", kimi):
            result = await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        kimi.assert_awaited_once_with("深度报告 宁德时代", "深度报告")
        self.assertEqual(result, "报告正文")

    async def test_enabled_feature_adds_internal_and_official_sources(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        evidence = [official_evidence()]
        history = [
            {
                "报告标题": "历史报告",
                "报告类型": "深度报告",
                "行业": "新能源",
                "公司/主体": "宁德时代",
                "核心结论": "历史结论",
            }
        ]

        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.extract_research_subject",
            return_value={
                "issuer": "宁德时代",
                "stock_code": None,
                "query": "宁德时代",
            },
        ) as extract_subject, patch(
            "app.providers.collect_official_evidence",
            AsyncMock(return_value=evidence),
        ) as collect, patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="知识库内容")
        ), patch.object(
            main, "read_recent_news_records", AsyncMock()
        ) as read_news, patch.object(
            main, "query_bitable_records", AsyncMock(return_value=history)
        ) as read_history, patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news-table"
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", "report-table"
        ):
            result = await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        extract_subject.assert_called_once_with("深度报告 宁德时代")
        read_news.assert_not_awaited()
        read_history.assert_awaited_once_with("report-table", "宁德时代", limit=10)
        collect.assert_awaited_once_with("深度报告 宁德时代")

        prompt = kimi.await_args.args[0]
        self.assertIn("【知识库参考资料】\n知识库内容", prompt)
        self.assertNotIn("【舆情池参考资料】", prompt)
        self.assertIn("【历史报告参考资料】", prompt)
        self.assertIn("历史结论", prompt)
        self.assertIn("官方资料：", prompt)
        self.assertIn("https://static.cninfo.com.cn/finalpage/test.pdf", prompt)
        self.assertIn("来源索引由系统另行追加", prompt)

        self.assertIn("【官方资料索引】", result)
        self.assertIn("metadata_verified", result)
        self.assertGreater(
            result.index("【官方资料索引】"),
            result.index("【系统提示】"),
        )

    async def test_no_evidence_omits_official_prompt_and_index(self) -> None:
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
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", None
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", None
        ):
            result = await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        prompt = kimi.await_args.args[0]
        self.assertNotIn("官方资料：", prompt)
        self.assertNotIn("来源索引由系统另行追加", prompt)
        self.assertEqual(result, "报告正文")

    async def test_orchestrator_exception_does_not_block_report(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.extract_research_subject",
            return_value={"issuer": "宁德时代", "stock_code": None, "query": "宁德时代"},
        ), patch(
            "app.providers.collect_official_evidence",
            AsyncMock(side_effect=RuntimeError("provider failed")),
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", None
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", None
        ):
            result = await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        self.assertEqual(result, "报告正文")
        self.assertNotIn("官方资料：", kimi.await_args.args[0])

    async def test_none_subject_degrades_safely(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        read_history = AsyncMock()
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.extract_research_subject", return_value=None
        ), patch(
            "app.providers.collect_official_evidence", AsyncMock(return_value=[])
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(
            main, "query_bitable_records", read_history
        ), patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", "report-table"
        ):
            result = await main.generate_deep_report("深度报告", "深度报告")

        read_history.assert_not_awaited()
        self.assertIn("未识别到有效研究主体，未查询历史报告。", kimi.await_args.args[0])
        self.assertEqual(result, "报告正文")

    async def test_subject_extraction_exception_degrades_safely(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.extract_research_subject",
            side_effect=ValueError("invalid subject"),
        ), patch(
            "app.providers.collect_official_evidence", AsyncMock(return_value=[])
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", None
        ):
            result = await main.generate_deep_report("深度报告", "深度报告")

        kimi.assert_awaited_once()
        self.assertEqual(result, "报告正文")

    async def test_empty_query_skips_history_lookup(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        read_history = AsyncMock()
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.extract_research_subject",
            return_value={"issuer": None, "stock_code": None, "query": ""},
        ), patch(
            "app.providers.collect_official_evidence", AsyncMock(return_value=[])
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(
            main, "query_bitable_records", read_history
        ), patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", "report-table"
        ):
            await main.generate_deep_report("深度报告", "深度报告")

        read_history.assert_not_awaited()

    async def test_evidence_formatting_exception_still_calls_kimi(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.extract_research_subject",
            return_value={"issuer": "宁德时代", "stock_code": None, "query": "宁德时代"},
        ), patch(
            "app.providers.collect_official_evidence",
            AsyncMock(return_value=[official_evidence()]),
        ), patch(
            "app.providers.format_evidence_for_report",
            side_effect=ValueError("format failed"),
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", None
        ):
            result = await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        kimi.assert_awaited_once()
        self.assertNotIn("官方资料：", kimi.await_args.args[0])
        self.assertEqual(result, "报告正文")

    async def test_evidence_index_exception_does_not_fail_report(self) -> None:
        kimi = AsyncMock(return_value="报告正文")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.extract_research_subject",
            return_value={"issuer": "宁德时代", "stock_code": None, "query": "宁德时代"},
        ), patch(
            "app.providers.collect_official_evidence",
            AsyncMock(return_value=[official_evidence()]),
        ), patch(
            "app.providers.format_evidence_for_report", return_value="格式化资料"
        ), patch(
            "app.providers.format_evidence_index",
            side_effect=ValueError("index failed"),
        ), patch.object(
            main, "read_knowledge_records", AsyncMock(return_value="")
        ), patch.object(
            main, "call_kimi", kimi
        ), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", None
        ):
            result = await main.generate_deep_report("深度报告 宁德时代", "深度报告")

        self.assertEqual(result, "报告正文")


if __name__ == "__main__":
    unittest.main()
