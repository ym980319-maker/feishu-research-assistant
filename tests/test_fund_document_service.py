from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock

from app.services.fund_analysis_service import handle_fund_analysis
from app.services.fund_document_service import (
    FUND_DOCUMENT_FIELDS,
    FundDocumentService,
    extract_fund_document_fields,
    fund_document_fields_to_json,
)


PARSED_FUND_DOCUMENT = """
易方达示例基金合同

一、产品基本信息
基金名称：易方达示例基金
基金类型：混合型证券投资基金

二、投资策略
采用自上而下与自下而上相结合的投资方法。

三、投资范围
投资于依法发行上市的股票、债券和货币市场工具。

四、风险揭示
可能面临市场风险、信用风险及流动性风险。

五、基金经理及管理人
基金管理人：示例基金管理有限公司
基金经理：张三
"""


class FundDocumentExtractionTests(unittest.TestCase):
    def test_extracts_five_required_sections_from_parsed_text(self) -> None:
        result = extract_fund_document_fields(PARSED_FUND_DOCUMENT)

        self.assertEqual(tuple(result), FUND_DOCUMENT_FIELDS)
        self.assertIn("基金名称：易方达示例基金", result["产品信息"])
        self.assertIn("自上而下", result["投资策略"])
        self.assertIn("股票、债券", result["投资范围"])
        self.assertIn("流动性风险", result["风险因素"])
        self.assertIn("基金经理：张三", result["管理团队"])

    def test_accepts_multiple_pdf_or_word_texts(self) -> None:
        result = extract_fund_document_fields(
            [
                "投资策略：主要采用资产配置策略。",
                "风险因素：本基金可能面临利率风险。",
            ]
        )

        self.assertEqual(result["投资策略"], "主要采用资产配置策略。")
        self.assertEqual(result["风险因素"], "本基金可能面临利率风险。")

    def test_missing_sections_remain_empty_without_inference(self) -> None:
        result = extract_fund_document_fields("这是一段没有章节标签的正文。")

        self.assertEqual(result, {field: "" for field in FUND_DOCUMENT_FIELDS})

    def test_outputs_valid_stable_json(self) -> None:
        service = FundDocumentService()

        payload = json.loads(service.extract_json(PARSED_FUND_DOCUMENT))

        self.assertEqual(tuple(payload), FUND_DOCUMENT_FIELDS)
        self.assertIn("示例基金管理有限公司", payload["管理团队"])

    def test_json_formatter_drops_unknown_fields(self) -> None:
        payload = json.loads(
            fund_document_fields_to_json(
                {"产品信息": "产品资料", "未经支持字段": "不应输出"}
            )
        )

        self.assertEqual(tuple(payload), FUND_DOCUMENT_FIELDS)
        self.assertNotIn("未经支持字段", payload)


class FundDocumentRouteIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_fund_route_structures_documents_before_final_kimi_call(self) -> None:
        model = AsyncMock(return_value="基金投资决策报告")
        knowledge = AsyncMock(return_value="基金研究框架")
        researcher = AsyncMock(return_value={})

        result = await handle_fund_analysis(
            "分析示例基金",
            model,
            knowledge,
            researcher,
            documents=PARSED_FUND_DOCUMENT,
        )

        self.assertEqual(result, "基金投资决策报告")
        model.assert_awaited_once()
        prompt, task_type = model.await_args.args
        self.assertEqual(task_type, "基金产品研究")
        self.assertIn('"产品信息":', prompt)
        self.assertIn('"投资策略":', prompt)
        self.assertIn('"投资范围":', prompt)
        self.assertIn('"风险因素":', prompt)
        self.assertIn('"管理团队":', prompt)
        self.assertIn("基金经理：张三", prompt)
        self.assertNotIn("未经支持字段", prompt)

    async def test_no_document_keeps_existing_fund_route(self) -> None:
        model = AsyncMock(return_value="无附件基金报告")

        result = await handle_fund_analysis(
            "分析示例基金",
            model,
            AsyncMock(return_value=""),
            AsyncMock(return_value={}),
        )

        self.assertEqual(result, "无附件基金报告")
        model.assert_awaited_once()
        self.assertIn(
            "未提供基金合同、募集说明书或定期报告",
            model.await_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
