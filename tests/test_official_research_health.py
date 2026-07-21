from __future__ import annotations

import json
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


class _Request:
    def __init__(self, text: str, message_id: str = "message-health"):
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


class _Provider:
    def __init__(self, name: str, result=None, error: Exception | None = None):
        self.name = name
        self.result = [] if result is None else result
        self.error = error
        self.calls = 0

    async def search(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class OfficialResearchHealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.PROCESSING_MESSAGE_IDS.clear()
        main.PROCESSED_MESSAGE_IDS.clear()

    async def test_exact_command_is_intercepted_before_task_classification(self) -> None:
        reply = AsyncMock()
        health = AsyncMock(return_value="诊断结果")
        with patch.object(main, "handle_official_research_health", health), patch.object(
            main, "reply_feishu_message", reply
        ), patch.object(main, "detect_task_type") as detect, patch.object(
            main, "call_kimi", AsyncMock()
        ) as kimi, patch.object(main, "call_deepseek", AsyncMock()) as deepseek, patch.object(
            main, "write_knowledge_record", AsyncMock()
        ) as write_knowledge, patch.object(
            main, "write_report_record", AsyncMock()
        ) as write_report, patch.object(
            main, "create_feishu_doc", AsyncMock()
        ) as create_doc, patch.object(
            main, "write_task_record", AsyncMock()
        ) as write_task:
            result = await main.feishu_events(_Request("  官方资料状态  "))

        self.assertEqual(result["msg"], "official research health checked")
        health.assert_awaited_once_with()
        reply.assert_awaited_once_with("message-health", "诊断结果")
        detect.assert_not_called()
        kimi.assert_not_awaited()
        deepseek.assert_not_awaited()
        write_knowledge.assert_not_awaited()
        write_report.assert_not_awaited()
        create_doc.assert_not_awaited()
        write_task.assert_not_awaited()

    async def test_similar_text_does_not_trigger_health_command(self) -> None:
        health = AsyncMock()
        with patch.object(main, "handle_official_research_health", health), patch.object(
            main, "reply_feishu_message", AsyncMock()
        ), patch.object(main, "call_kimi", AsyncMock(return_value="摘要")) as kimi, patch.object(
            main, "write_knowledge_record", AsyncMock()
        ), patch.object(main, "write_task_record", AsyncMock()):
            await main.feishu_events(
                _Request("请分析官方资料状态变化", message_id="message-similar")
            )

        health.assert_not_awaited()
        kimi.assert_awaited_once()

    async def test_feature_off_skips_all_providers_but_checks_tables(self) -> None:
        get_providers = MagicMock()
        table_check = AsyncMock(return_value="可用")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=False
        ), patch(
            "app.providers.registry.get_enabled_providers", get_providers
        ), patch.object(main, "check_bitable_read_status", table_check), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news"
        ), patch.object(main, "FEISHU_KNOWLEDGE_TABLE_ID", "knowledge"), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", "report"
        ):
            result = await main.handle_official_research_health()

        get_providers.assert_not_called()
        self.assertEqual(table_check.await_count, 3)
        self.assertIn("功能开关：关闭", result)
        self.assertIn("巨潮资讯 Provider：未检查（功能关闭）", result)
        self.assertIn("工信部 Provider：未检查（功能关闭）", result)

    async def test_all_checks_succeed(self) -> None:
        cninfo = _Provider("cninfo", result=[object()])
        miit = _Provider("miit", result=[])
        table_check = AsyncMock(side_effect=["可用", "可用（暂无记录）", "可用"])
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.registry.get_enabled_providers", return_value=[cninfo, miit]
        ), patch.object(main, "check_bitable_read_status", table_check), patch.object(
            main, "FEISHU_NEWS_TABLE_ID", "news"
        ), patch.object(main, "FEISHU_KNOWLEDGE_TABLE_ID", "knowledge"), patch.object(
            main, "FEISHU_REPORT_TABLE_ID", "report"
        ):
            result = await main.handle_official_research_health()

        self.assertIn("功能开关：开启", result)
        self.assertIn("巨潮资讯 Provider：可用（返回 1 条）", result)
        self.assertIn("工信部 Provider：可用（暂无结果）", result)
        self.assertIn("知识库：可用（暂无记录）", result)
        self.assertIn("检查时间：", result)
        self.assertEqual(cninfo.calls, 1)
        self.assertEqual(miit.calls, 1)

    async def test_one_provider_failure_does_not_stop_other_checks(self) -> None:
        cninfo = _Provider("cninfo", error=TimeoutError("sensitive response body"))
        miit = _Provider("miit", result=[object()])
        table_check = AsyncMock(return_value="可用")
        with patch(
            "app.providers.registry.official_research_enabled", return_value=True
        ), patch(
            "app.providers.registry.get_enabled_providers", return_value=[cninfo, miit]
        ), patch.object(main, "check_bitable_read_status", table_check):
            result = await main.handle_official_research_health()

        self.assertIn("巨潮资讯 Provider：异常（TimeoutError）", result)
        self.assertIn("工信部 Provider：可用（返回 1 条）", result)
        self.assertEqual(table_check.await_count, 3)
        self.assertNotIn("sensitive response body", result)

    async def test_table_not_configured(self) -> None:
        with patch.object(main, "FEISHU_BITABLE_APP_TOKEN", "app-token"):
            result = await main.check_bitable_read_status(None)

        self.assertEqual(result, "未配置")

    async def test_empty_table_is_available(self) -> None:
        response = MagicMock()
        response.json.return_value = {"code": 0, "data": {"items": []}}
        client = AsyncMock()
        client.get.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        with patch.object(main, "FEISHU_BITABLE_APP_TOKEN", "app-token"), patch.object(
            main, "get_tenant_access_token", AsyncMock(return_value="secret-token")
        ), patch.object(main.httpx, "AsyncClient", return_value=context):
            result = await main.check_bitable_read_status("table-id")

        self.assertEqual(result, "可用（暂无记录）")
        client.get.assert_awaited_once()
        self.assertEqual(client.get.await_args.kwargs["params"], {"page_size": 1})
        response.raise_for_status.assert_called_once_with()

    async def test_table_failure_isolated_and_sanitized(self) -> None:
        table_check = AsyncMock(
            side_effect=[RuntimeError("token=abc secret=xyz"), "可用", "可用"]
        )
        with patch(
            "app.providers.registry.official_research_enabled", return_value=False
        ), patch.object(main, "check_bitable_read_status", table_check):
            result = await main.handle_official_research_health()

        self.assertIn("舆情池：异常（RuntimeError）", result)
        self.assertIn("知识库：可用", result)
        self.assertIn("报告库：可用", result)
        self.assertNotIn("token", result.lower())
        self.assertNotIn("secret", result.lower())
        self.assertNotIn("abc", result.lower())
        self.assertNotIn("xyz", result.lower())

    async def test_existing_routes_remain_unchanged(self) -> None:
        cases = (
            ("投研日报", "daily"),
            ("深度报告 宁德时代", "deep"),
            ("梳理今天舆情", "news"),
        )
        for index, (text, expected) in enumerate(cases):
            with self.subTest(text=text):
                daily = AsyncMock(return_value="日报")
                deep = AsyncMock(return_value="深度报告")
                deepseek = AsyncMock(return_value="舆情")
                kimi = AsyncMock(return_value="舆情")
                with patch.object(main, "handle_daily_report", daily), patch.object(
                    main, "generate_deep_report", deep
                ), patch.object(main, "call_deepseek", deepseek), patch.object(
                    main, "call_kimi", kimi
                ), patch.object(
                    main, "write_news_record", AsyncMock()
                ), patch.object(main, "write_report_record", AsyncMock()), patch.object(
                    main, "create_feishu_doc", AsyncMock(return_value="https://example.com/doc")
                ), patch.object(main, "write_task_record", AsyncMock()), patch.object(
                    main, "reply_feishu_message", AsyncMock()
                ), patch.object(main, "handle_official_research_health", AsyncMock()) as health:
                    await main.feishu_events(
                        _Request(text, message_id=f"message-route-{index}")
                    )

                health.assert_not_awaited()
                if expected == "daily":
                    daily.assert_awaited_once()
                elif expected == "deep":
                    deep.assert_awaited_once()
                else:
                    kimi.assert_awaited_once()
                    deepseek.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
