from __future__ import annotations

import asyncio
import json
import sys
import unittest
from types import ModuleType
from unittest.mock import ANY, AsyncMock, MagicMock, patch


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

from app import main


class _Request:
    def __init__(self, message_id: str = "file-message-1") -> None:
        self.payload = {
            "event": {
                "message": {
                    "message_id": message_id,
                    "message_type": "file",
                    "content": json.dumps(
                        {
                            "file_key": "file-key-1",
                            "file_name": "fund-report.pdf",
                        }
                    ),
                }
            }
        }

    async def json(self):
        return self.payload


class FileMessageProcessingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main.PROCESSING_MESSAGE_IDS.clear()
        main.PROCESSED_MESSAGE_IDS.clear()

    def tearDown(self) -> None:
        main.PROCESSING_MESSAGE_IDS.clear()
        main.PROCESSED_MESSAGE_IDS.clear()

    async def test_successful_file_event_replies_once_and_is_deduplicated(self) -> None:
        reply = AsyncMock()
        summarize = AsyncMock(return_value="Kimi 文件摘要")
        write_knowledge = AsyncMock()

        with patch.object(
            main,
            "download_feishu_message_file",
            AsyncMock(return_value="downloads/fund-report.pdf"),
        ) as download, patch.object(
            main,
            "extract_text_from_file",
            return_value="基金PDF正文",
        ), patch.object(
            main,
            "summarize_file_with_kimi",
            summarize,
        ), patch.object(
            main,
            "write_knowledge_record",
            write_knowledge,
        ), patch.object(main, "reply_feishu_message", reply):
            first = await main.feishu_events(_Request())
            second = await main.feishu_events(_Request())

        self.assertEqual(first, {"code": 0, "msg": "file processed"})
        self.assertEqual(
            second,
            {"code": 0, "msg": "duplicate completed ignored"},
        )
        download.assert_awaited_once()
        summarize.assert_awaited_once_with("fund-report.pdf", "基金PDF正文")
        write_knowledge.assert_awaited_once()
        reply.assert_awaited_once()
        self.assertIn("Kimi 文件摘要", reply.await_args.args[1])
        self.assertIn("已写入飞书多维表格", reply.await_args.args[1])

    async def test_concurrent_feishu_retry_is_ignored_while_file_is_processing(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_summary(file_name: str, file_text: str) -> str:
            started.set()
            await release.wait()
            return "唯一文件摘要"

        reply = AsyncMock()
        with patch.object(
            main,
            "download_feishu_message_file",
            AsyncMock(return_value="downloads/fund-report.pdf"),
        ) as download, patch.object(
            main,
            "extract_text_from_file",
            return_value="基金PDF正文",
        ), patch.object(
            main,
            "summarize_file_with_kimi",
            side_effect=slow_summary,
        ) as summarize, patch.object(
            main,
            "write_knowledge_record",
            AsyncMock(),
        ), patch.object(main, "reply_feishu_message", reply):
            first_task = asyncio.create_task(
                main.feishu_events(_Request("concurrent-file-message"))
            )
            await started.wait()
            retry_result = await main.feishu_events(
                _Request("concurrent-file-message")
            )
            self.assertIn(
                "concurrent-file-message",
                main.PROCESSING_MESSAGE_IDS,
            )
            third_retry_result = await main.feishu_events(
                _Request("concurrent-file-message")
            )
            release.set()
            first_result = await first_task

        self.assertEqual(first_result["msg"], "file processed")
        self.assertEqual(retry_result["msg"], "duplicate processing ignored")
        self.assertEqual(
            third_retry_result["msg"],
            "duplicate processing ignored",
        )
        download.assert_awaited_once()
        summarize.assert_awaited_once()
        reply.assert_awaited_once_with(
            "concurrent-file-message",
            ANY,
        )

    async def test_knowledge_write_failure_has_initialized_system_tip(self) -> None:
        reply = AsyncMock()
        with patch.object(
            main,
            "download_feishu_message_file",
            AsyncMock(return_value="downloads/fund-report.pdf"),
        ), patch.object(
            main,
            "extract_text_from_file",
            return_value="基金PDF正文",
        ), patch.object(
            main,
            "summarize_file_with_kimi",
            AsyncMock(return_value="Kimi 文件摘要"),
        ), patch.object(
            main,
            "write_knowledge_record",
            AsyncMock(side_effect=RuntimeError("table unavailable")),
        ), patch.object(main, "reply_feishu_message", reply):
            result = await main.feishu_events(_Request("knowledge-failure"))

        self.assertEqual(result["msg"], "file processed")
        reply.assert_awaited_once()
        response_text = reply.await_args.args[1]
        self.assertIn("Kimi 文件摘要", response_text)
        self.assertIn("写入知识库素材失败", response_text)
        self.assertNotIn("system_tip", response_text)

    async def test_file_processing_failure_replies_once(self) -> None:
        reply = AsyncMock()
        with patch.object(
            main,
            "download_feishu_message_file",
            AsyncMock(side_effect=RuntimeError("download unavailable")),
        ), patch.object(main, "reply_feishu_message", reply):
            result = await main.feishu_events(_Request("download-failure"))

        self.assertEqual(result["msg"], "file processed")
        reply.assert_awaited_once()
        self.assertIn("处理失败", reply.await_args.args[1])
        self.assertIn("download-failure", main.PROCESSED_MESSAGE_IDS)


class KimiRetryImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_kimi_timeout_retry_has_module_level_asyncio(self) -> None:
        success_response = MagicMock()
        success_response.json.return_value = {
            "choices": [{"message": {"content": "重试成功"}}]
        }
        client = AsyncMock()
        client.post.side_effect = [
            main.httpx.TimeoutException("first timeout"),
            success_response,
        ]
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch.object(
            main.httpx,
            "AsyncClient",
            return_value=context,
        ), patch.object(main, "KIMI_API_KEY", "test-kimi-key"), patch.object(
            main.asyncio,
            "sleep",
            AsyncMock(),
        ) as sleep:
            result = await main.call_kimi("测试超时重试", "基金产品研究")

        self.assertEqual(result, "重试成功")
        self.assertEqual(client.post.await_count, 2)
        sleep.assert_awaited_once_with(2)


if __name__ == "__main__":
    unittest.main()
