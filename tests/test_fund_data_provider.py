from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.providers.fund_data_provider import (
    FUND_BASIC_URL,
    FUND_DATA_SOURCE,
    FUND_DETAIL_URL,
    FUND_HOLDINGS_URL,
    FundDataProvider,
    FundDataProviderError,
    InvalidFundCodeError,
    normalize_fund_data,
)
from app.services.fund_investment_decision_service import (
    extract_fund_code,
    generate_fund_investment_decision,
)


def _basic_payload():
    return {
        "ErrCode": 0,
        "Datas": {
            "FCODE": "005827",
            "SHORTNAME": "易方达蓝筹精选混合",
            "FTYPE": "混合型-偏股",
            "ESTABDATE": "2018-09-05",
            "DWJZ": "1.5345",
            "FSRQ": "2026-07-21",
            "ENDNAV": "20415804144.76",
            "FEGMRQ": "2026-06-30",
            "SYL_Z": "0.53",
        },
    }


def _detail_payload():
    return {
        "ErrCode": 0,
        "Datas": {
            "FCODE": "005827",
            "JJJL": "张坤,杨思亮,何一铖",
            "SYL_Y": "0.57",
            "SYL_3Y": "-13.14",
            "SYL_6Y": "-19",
            "SYL_1N": "-16.85",
        },
    }


def _holdings_payload():
    return {
        "ErrCode": 0,
        "Expansion": "2026-06-30",
        "Datas": {
            "fundStocks": [
                {
                    "GPDM": "00700",
                    "GPJC": "腾讯控股",
                    "JZBL": "5.72",
                    "PCTNVCHGTYPE": "减持",
                    "PCTNVCHG": "-3.88",
                }
            ],
            "fundboods": [],
            "fundfofs": [],
        },
    }


class FundDataNormalizationTests(unittest.TestCase):
    def test_normalizes_all_required_fund_fields(self) -> None:
        result = normalize_fund_data(
            "005827",
            _basic_payload(),
            _detail_payload(),
            _holdings_payload(),
        )

        self.assertEqual(
            set(result),
            {
                "基金名称",
                "基金代码",
                "基金类型",
                "成立日期",
                "基金经理",
                "基金规模",
                "最新净值",
                "历史收益",
                "持仓信息",
            },
        )
        self.assertEqual(result["基金名称"], "易方达蓝筹精选混合")
        self.assertEqual(result["基金代码"], "005827")
        self.assertEqual(result["基金类型"], "混合型-偏股")
        self.assertEqual(result["成立日期"], "2018-09-05")
        self.assertEqual(result["基金经理"], ["张坤", "杨思亮", "何一铖"])
        self.assertEqual(
            result["基金规模"],
            {"金额": "204.16亿元", "截止日期": "2026-06-30"},
        )
        self.assertEqual(
            result["最新净值"],
            {"单位净值": "1.5345", "净值日期": "2026-07-21"},
        )
        self.assertEqual(result["历史收益"]["近1年"], "-16.85%")
        self.assertEqual(
            result["持仓信息"][0],
            {
                "资产类型": "股票",
                "证券代码": "00700",
                "证券名称": "腾讯控股",
                "占净值比例": "5.72%",
                "持仓变化": "减持",
                "较上期变化": "-3.88%",
                "披露日期": "2026-06-30",
            },
        )

    def test_invalid_code_is_rejected(self) -> None:
        with self.assertRaises(InvalidFundCodeError):
            normalize_fund_data("5827", {}, {}, {})

    def test_empty_public_payload_is_not_treated_as_fund_data(self) -> None:
        with self.assertRaises(FundDataProviderError):
            normalize_fund_data("005827", {"Datas": []}, {}, {})


class FundDataProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_three_real_data_dimensions_once(self) -> None:
        responses = []
        for payload in (
            _basic_payload(),
            _detail_payload(),
            _holdings_payload(),
        ):
            response = MagicMock()
            response.json.return_value = payload
            responses.append(response)

        client = AsyncMock()
        client.get.side_effect = responses
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch(
            "app.providers.fund_data_provider.httpx.AsyncClient",
            return_value=context,
        ) as client_factory:
            result = await FundDataProvider(timeout=8).get_fund_data("005827")

        self.assertEqual(result["基金代码"], "005827")
        self.assertEqual(client.get.await_count, 3)
        requested_urls = {call.args[0] for call in client.get.await_args_list}
        self.assertEqual(
            requested_urls,
            {FUND_BASIC_URL, FUND_DETAIL_URL, FUND_HOLDINGS_URL},
        )
        for call in client.get.await_args_list:
            self.assertEqual(call.kwargs["params"]["FCODE"], "005827")
        client_factory.assert_called_once()
        self.assertEqual(client_factory.call_args.kwargs["timeout"], 8)

    async def test_invalid_code_does_not_call_http(self) -> None:
        with patch(
            "app.providers.fund_data_provider.httpx.AsyncClient"
        ) as client_factory:
            with self.assertRaises(InvalidFundCodeError):
                await FundDataProvider().get_fund_data("abc")

        client_factory.assert_not_called()

    async def test_total_source_failure_returns_safe_provider_error(self) -> None:
        client = AsyncMock()
        client.get.side_effect = RuntimeError("network unavailable")
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch(
            "app.providers.fund_data_provider.httpx.AsyncClient",
            return_value=context,
        ):
            with self.assertRaisesRegex(
                FundDataProviderError,
                "基金数据源暂时不可用",
            ):
                await FundDataProvider().get_fund_data("005827")


class FundDecisionProviderIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_extracts_six_digit_fund_code_only(self) -> None:
        self.assertEqual(extract_fund_code("请分析基金005827"), "005827")
        self.assertEqual(extract_fund_code("请分析基金5827"), "")

    async def test_fund_data_enters_the_single_kimi_prompt(self) -> None:
        provider = AsyncMock()
        provider.get_fund_data.return_value = normalize_fund_data(
            "005827",
            _basic_payload(),
            _detail_payload(),
            _holdings_payload(),
        )
        researcher = AsyncMock(return_value={})
        knowledge = AsyncMock(return_value="内部基金框架")
        model = AsyncMock(return_value="基金投决报告")

        result = await generate_fund_investment_decision(
            "请分析基金005827",
            model,
            knowledge,
            evidence_researcher=researcher,
            fund_data_provider=provider,
        )

        self.assertEqual(result, "基金投决报告")
        provider.get_fund_data.assert_awaited_once_with("005827")
        model.assert_awaited_once()
        prompt = model.await_args.args[0]
        self.assertIn(FUND_DATA_SOURCE, prompt)
        self.assertIn('"基金代码": "005827"', prompt)
        self.assertIn('"单位净值": "1.5345"', prompt)
        self.assertIn('"证券名称": "腾讯控股"', prompt)

    async def test_provider_failure_still_generates_without_fabrication(self) -> None:
        provider = AsyncMock()
        provider.get_fund_data.side_effect = RuntimeError("secret detail")
        model = AsyncMock(return_value="缺数基金投决报告")

        result = await generate_fund_investment_decision(
            "分析005827基金",
            model,
            AsyncMock(return_value=""),
            evidence_researcher=AsyncMock(return_value={}),
            fund_data_provider=provider,
        )

        self.assertEqual(result, "缺数基金投决报告")
        prompt = model.await_args.args[0]
        self.assertIn("基金代码 005827 的公开基金数据暂不可用", prompt)
        self.assertNotIn("secret detail", prompt)


if __name__ == "__main__":
    unittest.main()
