"""Real public fund data provider backed by Eastmoney/Tiantian Fund APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any

import httpx


FUND_BASIC_URL = (
    "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation"
)
FUND_DETAIL_URL = (
    "https://fundmobapi.eastmoney.com/FundMApi/FundBaseTypeInformation.ashx"
)
FUND_HOLDINGS_URL = (
    "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNInverstPosition"
)
FUND_DATA_SOURCE = "天天基金公开接口（东方财富）"
FUND_CODE_PATTERN = re.compile(r"^\d{6}$")

HISTORY_RETURN_FIELDS = {
    "SYL_Z": "近1周",
    "SYL_Y": "近1月",
    "SYL_3Y": "近3月",
    "SYL_6Y": "近6月",
    "SYL_1N": "近1年",
    "SYL_2N": "近2年",
    "SYL_3N": "近3年",
    "SYL_5N": "近5年",
    "SYL_JN": "今年以来",
    "SYL_LN": "成立以来",
}


class FundDataProviderError(RuntimeError):
    """Raised when the public fund data source cannot provide valid data."""


class InvalidFundCodeError(ValueError):
    """Raised when a fund code is not exactly six digits."""


def validate_fund_code(value: str) -> str:
    fund_code = str(value or "").strip()
    if not FUND_CODE_PATTERN.fullmatch(fund_code):
        raise InvalidFundCodeError("基金代码必须为6位数字")
    return fund_code


def _text(value: Any) -> str:
    return str(value or "").strip()


def _data_mapping(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("Datas")
    return data if isinstance(data, Mapping) else {}


def _percent(value: Any) -> str:
    normalized = _text(value)
    if not normalized or normalized in {"--", "-"}:
        return ""
    return normalized if normalized.endswith("%") else f"{normalized}%"


def _fund_scale(value: Any, published_date: Any) -> dict[str, str]:
    normalized = _text(value)
    if not normalized:
        return {}
    try:
        amount = (Decimal(normalized) / Decimal("100000000")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        amount_text = format(amount, "f").rstrip("0").rstrip(".")
        formatted = f"{amount_text}亿元"
    except InvalidOperation:
        formatted = normalized
    result = {"金额": formatted}
    date_text = _text(published_date)
    if date_text:
        result["截止日期"] = date_text
    return result


def _fund_managers(value: Any) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[,，、]", _text(value))
        if item.strip()
    ]


def _history_returns(*sources: Mapping[str, Any]) -> dict[str, str]:
    result = {}
    for field, label in HISTORY_RETURN_FIELDS.items():
        value = next(
            (_percent(source.get(field)) for source in sources if source.get(field)),
            "",
        )
        if value:
            result[label] = value
    return result


def _holding_items(payload: Mapping[str, Any] | None) -> list[dict[str, str]]:
    data = _data_mapping(payload)
    disclosure_date = _text(payload.get("Expansion")) if payload else ""
    groups = (
        ("fundStocks", "股票"),
        ("fundboods", "债券"),
        ("fundfofs", "基金"),
    )
    holdings = []
    for group_name, asset_type in groups:
        items = data.get(group_name)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            code = _text(
                item.get("GPDM")
                or item.get("ZQDM")
                or item.get("JJDM")
                or item.get("FCODE")
            )
            name = _text(
                item.get("GPJC")
                or item.get("ZQJC")
                or item.get("JJJC")
                or item.get("SHORTNAME")
            )
            if not (code or name):
                continue
            holding = {
                "资产类型": asset_type,
                "证券代码": code,
                "证券名称": name,
                "占净值比例": _percent(item.get("JZBL")),
            }
            change_type = _text(item.get("PCTNVCHGTYPE"))
            change = _percent(item.get("PCTNVCHG"))
            if change_type:
                holding["持仓变化"] = change_type
            if change:
                holding["较上期变化"] = change
            if disclosure_date:
                holding["披露日期"] = disclosure_date
            holdings.append(holding)
    return holdings


def normalize_fund_data(
    fund_code: str,
    basic_payload: Mapping[str, Any] | None,
    detail_payload: Mapping[str, Any] | None,
    holdings_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize provider payloads without filling absent source values."""
    code = validate_fund_code(fund_code)
    basic = _data_mapping(basic_payload)
    detail = _data_mapping(detail_payload)
    if not basic and not detail:
        raise FundDataProviderError(f"未找到基金代码 {code} 的公开基金数据")

    value_date = _text(basic.get("FSRQ") or detail.get("FSRQ"))
    nav_value = _text(basic.get("DWJZ") or detail.get("DWJZ"))
    latest_nav = {}
    if nav_value:
        latest_nav["单位净值"] = nav_value
    if value_date:
        latest_nav["净值日期"] = value_date

    return {
        "基金名称": _text(basic.get("SHORTNAME") or detail.get("SHORTNAME")),
        "基金代码": _text(basic.get("FCODE") or detail.get("FCODE")) or code,
        "基金类型": _text(basic.get("FTYPE") or detail.get("FTYPE")),
        "成立日期": _text(basic.get("ESTABDATE") or detail.get("ESTABDATE")),
        "基金经理": _fund_managers(detail.get("JJJL") or basic.get("JJJL")),
        "基金规模": _fund_scale(
            basic.get("ENDNAV") or detail.get("ENDNAV"),
            basic.get("FEGMRQ") or detail.get("FEGMRQ"),
        ),
        "最新净值": latest_nav,
        "历史收益": _history_returns(basic, detail),
        "持仓信息": _holding_items(holdings_payload),
    }


class FundDataProvider:
    """Fetch structured data for one six-digit Chinese public fund code."""

    def __init__(self, *, timeout: float = 15.0) -> None:
        self.timeout = timeout

    @staticmethod
    def _params(fund_code: str) -> dict[str, str]:
        return {
            "FCODE": fund_code,
            "deviceid": "feishu-research-assistant",
            "plat": "Android",
            "product": "EFund",
            "version": "6.3.8",
            "serverVersion": "6.3.8",
            "appType": "ttjj",
        }

    @staticmethod
    async def _request(
        client: httpx.AsyncClient,
        url: str,
        params: Mapping[str, str],
    ) -> Mapping[str, Any]:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise FundDataProviderError("基金数据源返回了无效格式")
        error_code = payload.get("ErrCode")
        if error_code not in (None, 0, "0"):
            raise FundDataProviderError(
                "基金数据源返回错误，错误码：" + _text(error_code)
            )
        return payload

    async def get_fund_data(self, fund_code: str) -> dict[str, Any]:
        code = validate_fund_code(fund_code)
        params = self._params(code)
        headers = {
            "Accept": "application/json",
            "User-Agent": "feishu-research-assistant/1.0",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers=headers,
        ) as client:
            responses = await asyncio.gather(
                self._request(client, FUND_BASIC_URL, params),
                self._request(client, FUND_DETAIL_URL, params),
                self._request(client, FUND_HOLDINGS_URL, params),
                return_exceptions=True,
            )

        if all(isinstance(item, Exception) for item in responses):
            raise FundDataProviderError("基金数据源暂时不可用")
        basic_payload = responses[0] if isinstance(responses[0], Mapping) else None
        detail_payload = responses[1] if isinstance(responses[1], Mapping) else None
        holdings_payload = (
            responses[2] if isinstance(responses[2], Mapping) else None
        )
        return normalize_fund_data(
            code,
            basic_payload,
            detail_payload,
            holdings_payload,
        )
