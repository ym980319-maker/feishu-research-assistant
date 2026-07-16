"""国家统计局宏观数据源。

当前模块负责：
1. 请求国家统计局“国家数据”接口；
2. 统一处理超时、重试和异常；
3. 将原始返回结果转换为后续可写入飞书的结构。

本文件暂不接入主流程，避免影响现有已运行功能。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)


NBS_API_URL = "https://data.stats.gov.cn/easyquery.htm"

DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 2


class NBSDataError(RuntimeError):
    """国家统计局数据请求或解析失败。"""


def fetch_nbs_data(
    *,
    dbcode: str,
    rowcode: str,
    colcode: str,
    wds: list[dict[str, str]] | None = None,
    dfwds: list[dict[str, str]] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """请求国家统计局国家数据接口。

    Args:
        dbcode: 数据库代码，例如 hgyd、hgjd。
        rowcode: 行维度代码，通常为 zb。
        colcode: 列维度代码，通常为 sj。
        wds: 固定维度条件。
        dfwds: 查询筛选条件。
        timeout: 单次请求超时时间，单位为秒。
        max_retries: 最大请求次数。

    Returns:
        国家统计局接口返回的 JSON 数据。

    Raises:
        NBSDataError: 请求失败、响应不是 JSON，或接口返回异常。
    """
    params: dict[str, Any] = {
        "m": "QueryData",
        "dbcode": dbcode,
        "rowcode": rowcode,
        "colcode": colcode,
        "wds": _encode_conditions(wds or []),
        "dfwds": _encode_conditions(dfwds or []),
        "k1": int(time.time() * 1000),
        "h": "1",
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "application/json, text/javascript, "
            "*/*; q=0.01"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://data.stats.gov.cn/",
        "Origin": "https://data.stats.gov.cn",
        "Connection": "keep-alive",
    }

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            session = requests.Session()

            session.headers.update(headers)

            response = session.get(
                NBS_API_URL,
                params=params,
                timeout=timeout,
            ) 
            response.raise_for_status()

            payload = response.json()

            if not isinstance(payload, dict):
                raise NBSDataError("国家统计局接口返回格式不是 JSON 对象")

            return_code = payload.get("returncode")
            if return_code not in (None, 200, 200.0, "200"):
                message = payload.get("returndata") or payload.get("message")
                raise NBSDataError(
                    f"国家统计局接口返回异常：returncode={return_code}, "
                    f"message={message}"
                )

            return payload

        except (
            requests.RequestException,
            ValueError,
            NBSDataError,
        ) as exc:
            last_error = exc
            logger.warning(
                "国家统计局数据请求失败，第 %s/%s 次：%s",
                attempt,
                max_retries,
                exc,
            )

            if attempt < max_retries:
                time.sleep(DEFAULT_RETRY_DELAY_SECONDS * attempt)

    raise NBSDataError(
        f"国家统计局数据请求失败，已重试 {max_retries} 次：{last_error}"
    ) from last_error


def _encode_conditions(conditions: list[dict[str, str]]) -> str:
    """将查询条件转换为国家统计局接口需要的 JSON 字符串。"""
    import json

    return json.dumps(
        conditions,
        ensure_ascii=False,
        separators=(",", ":"),
    )
