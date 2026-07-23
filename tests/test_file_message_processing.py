from __future__ import annotations

import asyncio
import json
import sys
import unittest
from types import ModuleType
from unittest.mock import ANY, AsyncMock, MagicMock, mock_open, patch


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
    def __init__(
        self,
        message_id: str = "file-message-1",
        file_name: str = "research-report.pdf",
    ) -> None:
        self.payload = {
            "event": {
                "message": {
                    "message_id": message_id,
                    "message_type": "file",
                    "content": json.dumps(
                        {
                            "file_key": "file-key-1",
                            "file_name": file_name,
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

    async def asyncTearDown(self) -> None:
        tasks = tuple(main.BACKGROUND_TASKS)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        main.BACKGROUND_TASKS.clear()

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
            return_value="普通研报正文",
        ), patch.object(
            main,
            "summarize_file_with_kimi",
            summarize,
        ), patch.object(
            main,
            "write_knowledge_record",
            write_knowledge,
        ), patch.object(
            main,
            "handle_fund_analysis",
            AsyncMock(),
        ) as fund_analysis, patch.object(main, "reply_feishu_message", reply):
            first = await main.feishu_events(_Request())
            await asyncio.gather(*tuple(main.BACKGROUND_TASKS))
            second = await main.feishu_events(_Request())

        self.assertEqual(first, {"code": 0, "msg": "file processed"})
        self.assertEqual(
            second,
            {"code": 0, "msg": "duplicate completed ignored"},
        )
        download.assert_awaited_once()
        summarize.assert_awaited_once_with("research-report.pdf", "普通研报正文")
        fund_analysis.assert_not_awaited()
        write_knowledge.assert_awaited_once()
        reply.assert_awaited_once()
        self.assertEqual(reply.await_args.args[1], summarize.return_value)

    async def test_reply_uses_post_markdown_and_preserves_newlines(self) -> None:
        response = MagicMock()
        response.json.return_value = {"code": 0}
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        markdown = "# 产品尽调分析报告\r\n\r\n## 一、产品概况\r\n**报告声明**"

        with patch.object(
            main,
            "get_tenant_access_token",
            AsyncMock(return_value="tenant-token"),
        ), patch.object(main.httpx, "AsyncClient", return_value=context):
            result = await main.reply_feishu_message("message-1", markdown)

        self.assertEqual(result, {"code": 0})
        payload = client.post.await_args.kwargs["json"]
        self.assertEqual(payload["msg_type"], "post")
        rich_text = json.loads(payload["content"])["zh_cn"]
        self.assertEqual(rich_text["title"], "")
        markdown_node = rich_text["content"][0][0]
        self.assertEqual(markdown_node["tag"], "md")
        self.assertEqual(
            markdown_node["text"],
            "# 产品尽调分析报告\n\n## 一、产品概况\n**报告声明**",
        )

    async def test_download_file_has_os_available_for_path_creation(self) -> None:
        response = MagicMock(status_code=200, content=b"pdf-content", text="")
        client = AsyncMock()
        client.get.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        file_handle = mock_open()

        with patch.object(
            main,
            "get_tenant_access_token",
            AsyncMock(return_value="tenant-token"),
        ), patch.object(
            main.httpx,
            "AsyncClient",
            return_value=context,
        ), patch.object(
            main.os,
            "makedirs",
        ) as makedirs, patch.object(
            main.os.path,
            "join",
            return_value="downloads/fund-report.pdf",
        ) as join_path, patch("builtins.open", file_handle):
            result = await main.download_feishu_message_file(
                "message-id",
                "file-key",
                "fund-report.pdf",
            )

        self.assertEqual(result, "downloads/fund-report.pdf")
        makedirs.assert_called_once_with("downloads", exist_ok=True)
        join_path.assert_called_once_with("downloads", "fund-report.pdf")
        file_handle.assert_called_once_with("downloads/fund-report.pdf", "wb")
        file_handle().write.assert_called_once_with(b"pdf-content")

    async def test_fund_file_uses_only_fund_analysis_and_duplicate_replies_once(self) -> None:
        reply = AsyncMock()
        fund_analysis = AsyncMock(return_value="基金产品尽调分析报告")
        summarize = AsyncMock(return_value="不应生成的通用摘要")
        write_knowledge = AsyncMock()
        file_text = "基金合同与募集说明书正文，包含投资策略、投资范围和风险因素。"

        with patch.object(
            main,
            "download_feishu_message_file",
            AsyncMock(return_value="downloads/fund-contract.pdf"),
        ) as download, patch.object(
            main,
            "extract_text_from_file",
            return_value=file_text,
        ), patch.object(
            main,
            "handle_fund_analysis",
            fund_analysis,
        ), patch.object(
            main,
            "summarize_file_with_kimi",
            summarize,
        ), patch.object(
            main,
            "write_knowledge_record",
            write_knowledge,
        ), patch.object(main, "reply_feishu_message", reply), patch(
            "builtins.print"
        ) as print_log:
            first = await main.feishu_events(
                _Request("fund-file-message", "某基金合同.pdf")
            )
            await asyncio.gather(*tuple(main.BACKGROUND_TASKS))
            duplicate = await main.feishu_events(
                _Request("fund-file-message", "某基金合同.pdf")
            )

        self.assertEqual(first, {"code": 0, "msg": "file processed"})
        self.assertEqual(
            duplicate,
            {"code": 0, "msg": "duplicate completed ignored"},
        )
        download.assert_awaited_once()
        fund_analysis.assert_awaited_once()
        self.assertEqual(fund_analysis.await_args.kwargs["documents"], file_text)
        summarize.assert_not_awaited()
        write_knowledge.assert_awaited_once()
        reply.assert_awaited_once_with("fund-file-message", "基金产品尽调分析报告")
        messages = [
            " ".join(str(value) for value in call.args)
            for call in print_log.call_args_list
        ]
        self.assertTrue(
            any(
                f"基金分析输入文本长度: {len(file_text)}" in message
                for message in messages
            )
        )

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
            return_value="普通研报正文",
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
            await asyncio.gather(*tuple(main.BACKGROUND_TASKS))

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

    async def test_knowledge_write_failure_does_not_change_summary_reply(self) -> None:
        reply = AsyncMock()
        summary = AsyncMock(return_value="Kimi 文件摘要")
        with patch.object(
            main,
            "download_feishu_message_file",
            AsyncMock(return_value="downloads/fund-report.pdf"),
        ), patch.object(
            main,
            "extract_text_from_file",
            return_value="普通研报正文",
        ), patch.object(
            main,
            "summarize_file_with_kimi",
            summary,
        ), patch.object(
            main,
            "write_knowledge_record",
            AsyncMock(side_effect=RuntimeError("table unavailable")),
        ), patch.object(main, "reply_feishu_message", reply):
            result = await main.feishu_events(_Request("knowledge-failure"))
            await asyncio.gather(*tuple(main.BACKGROUND_TASKS))

        self.assertEqual(result["msg"], "file processed")
        reply.assert_awaited_once()
        self.assertEqual(reply.await_args.args[1], summary.return_value)

    async def test_slow_knowledge_write_does_not_block_reply_or_allow_retry(self) -> None:
        write_started = asyncio.Event()
        release_write = asyncio.Event()

        async def slow_write(user_text: str, summary_text: str) -> None:
            write_started.set()
            await release_write.wait()

        reply = AsyncMock()
        with patch.object(
            main,
            "download_feishu_message_file",
            AsyncMock(return_value="downloads/fund-report.pdf"),
        ) as download, patch.object(
            main,
            "extract_text_from_file",
            return_value="普通研报正文",
        ), patch.object(
            main,
            "summarize_file_with_kimi",
            AsyncMock(return_value="Kimi 文件摘要"),
        ) as summarize, patch.object(
            main,
            "write_knowledge_record",
            side_effect=slow_write,
        ) as write_knowledge, patch.object(main, "reply_feishu_message", reply):
            first_result = await main.feishu_events(_Request("slow-knowledge-write"))
            reply.assert_awaited_once_with("slow-knowledge-write", "Kimi 文件摘要")

            await write_started.wait()
            retry_result = await main.feishu_events(_Request("slow-knowledge-write"))

            release_write.set()
            await asyncio.gather(*tuple(main.BACKGROUND_TASKS))

        self.assertEqual(first_result["msg"], "file processed")
        self.assertEqual(retry_result["msg"], "duplicate completed ignored")
        download.assert_awaited_once()
        summarize.assert_awaited_once()
        write_knowledge.assert_awaited_once()
        reply.assert_awaited_once()

    async def test_knowledge_write_timeout_is_logged_and_skipped(self) -> None:
        never_finishes = asyncio.Event()
        self.assertEqual(main.KNOWLEDGE_WRITE_TIMEOUT_SECONDS, 10)

        async def hanging_write(user_text: str, summary_text: str) -> None:
            await never_finishes.wait()

        with patch.object(
            main,
            "write_knowledge_record",
            side_effect=hanging_write,
        ), patch.object(
            main,
            "KNOWLEDGE_WRITE_TIMEOUT_SECONDS",
            0.01,
        ), patch("builtins.print") as print_log:
            await main.write_knowledge_record_in_background("source", "summary")

        messages = [
            " ".join(str(value) for value in call.args)
            for call in print_log.call_args_list
        ]
        self.assertTrue(any("开始写入知识库" in message for message in messages))
        self.assertTrue(any("知识库写入失败" in message for message in messages))

    async def test_knowledge_write_success_logs_completion(self) -> None:
        with patch.object(
            main,
            "write_knowledge_record",
            AsyncMock(),
        ), patch("builtins.print") as print_log:
            await main.write_knowledge_record_in_background("source", "summary")

        messages = [
            " ".join(str(value) for value in call.args)
            for call in print_log.call_args_list
        ]
        self.assertTrue(any("开始写入知识库" in message for message in messages))
        self.assertTrue(any("知识库写入完成" in message for message in messages))

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
