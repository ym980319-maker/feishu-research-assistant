"""国家统计局官方数据发布页宏观数据源。"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from app.sources.nbs_indicators import NBS_INDICATORS


logger = logging.getLogger(__name__)

NBS_RELEASE_INDEX_URL = "https://www.stats.gov.cn/sj/zxfb/"
NBS_ALLOWED_HOST = "www.stats.gov.cn"

DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 2
DEFAULT_CACHE_TTL_SECONDS = 43200
NBS_CACHE_PATH = Path(".cache/nbs_macro_data.json")

REQUIRED_ITEM_FIELDS = {
    "indicator_key",
    "indicator",
    "value",
    "unit",
    "period",
    "frequency",
    "comparison",
    "source",
    "source_url",
    "published_at",
    "fetched_at",
}

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


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
    use_cache: bool = True,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    """读取国家统计局最新官方发布文章并返回标准化宏观数据。

    原 easyquery 调用参数继续保留以兼容现有调用方。当前页面数据源固定
    返回 ``NBS_INDICATORS`` 中定义的五项月度或月度累计指标。
    """
    del wds, dfwds

    if use_cache:
        cached = _load_cache(cache_ttl_seconds)
        if cached is not None:
            return cached

    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    try:
        index_html = _request_html(
            session,
            NBS_RELEASE_INDEX_URL,
            timeout=timeout,
            max_retries=max_retries,
        )
        article_urls = parse_release_index(index_html)
        records: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for indicator_key in NBS_INDICATORS:
            source_url = article_urls.get(indicator_key)
            if not source_url:
                error = "未找到匹配文章"
                errors.append({"indicator_key": indicator_key, "error": error})
                logger.warning("国家统计局指标提取失败：%s：%s", indicator_key, error)
                continue
            try:
                article_html = _request_html(
                    session,
                    source_url,
                    timeout=timeout,
                    max_retries=max_retries,
                )
                item = parse_indicator_article(indicator_key, article_html, source_url)
                _validate_indicator_item(item)
                records.append(item)
            except (NBSDataError, ValueError, TypeError, KeyError) as exc:
                error = str(exc) or type(exc).__name__
                errors.append({"indicator_key": indicator_key, "error": error})
                logger.warning("国家统计局指标提取失败：%s：%s", indicator_key, error)
    finally:
        session.close()

    if not records:
        raise NBSDataError("国家统计局所有指标提取失败")

    result = {
        "returncode": 200,
        "returndata": {
            "items": records,
            "records": records,
            "errors": errors,
            "success_count": len(records),
            "failure_count": len(errors),
            "source_type": "official_release_pages",
            "requested_dimensions": {
                "dbcode": dbcode,
                "rowcode": rowcode,
                "colcode": colcode,
            },
        },
    }
    if use_cache:
        _write_cache(result)
    return result


def parse_release_index(html: str) -> dict[str, str]:
    """从数据发布首页定位每项指标最新的官方文章。"""
    if not html.strip():
        raise NBSDataError("国家统计局数据发布首页为空")

    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        title = _normalize_text(anchor.get_text(" ", strip=True))
        if not title or "解读" in title:
            continue
        source_url = urljoin(NBS_RELEASE_INDEX_URL, anchor["href"])
        if not _is_allowed_release_url(source_url):
            continue
        for indicator_key, config in NBS_INDICATORS.items():
            if indicator_key in found:
                continue
            if all(term in title for term in config["title_terms"]):
                found[indicator_key] = source_url
    return found


def parse_indicator_article(
    indicator_key: str,
    html: str,
    source_url: str,
) -> dict[str, Any]:
    """严格按指标口径解析单篇国家统计局官方发布文章。"""
    config = NBS_INDICATORS.get(indicator_key)
    if not config:
        raise NBSDataError(f"未知国家统计局指标：{indicator_key}")
    if not _is_allowed_release_url(source_url):
        raise NBSDataError(f"国家统计局文章 URL 不受信任：{indicator_key}")
    if not html.strip():
        raise NBSDataError(f"国家统计局文章内容为空：{indicator_key}")

    soup = BeautifulSoup(html, "html.parser")
    title = _article_title(soup)
    text = _normalize_text(soup.get_text(" ", strip=True))
    compact_text = re.sub(r"\s+", "", text)
    if not title or not all(term in title for term in config["title_terms"]):
        raise NBSDataError(f"国家统计局文章标题口径不匹配：{indicator_key}")

    value_match = re.search(config["value_pattern"], compact_text)
    if not value_match:
        raise NBSDataError(f"国家统计局文章未提取到指标值：{indicator_key}")
    value = float(value_match.group("value"))
    if value_match.group("direction") in {"下降", "下跌"}:
        value = -value

    period = _extract_period(config["period_type"], title, compact_text)
    published_at = _extract_published_at(text)

    return {
        "indicator_key": indicator_key,
        "indicator": config["indicator"],
        "period": period,
        "value": value,
        "unit": config["unit"],
        "frequency": config["frequency"],
        "comparison": config["comparison"],
        "source": "国家统计局",
        "source_url": source_url,
        "published_at": published_at,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _validate_indicator_item(item: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_ITEM_FIELDS if item.get(field) in (None, "")]
    if missing:
        raise NBSDataError(f"指标缺少必需字段：{', '.join(sorted(missing))}")
    if isinstance(item["value"], bool) or not isinstance(item["value"], (int, float)):
        raise NBSDataError("指标值不是数值类型")
    if not _is_allowed_release_url(str(item["source_url"])):
        raise NBSDataError("指标来源 URL 不受信任")


def _load_cache(cache_ttl_seconds: int) -> dict[str, Any] | None:
    if cache_ttl_seconds <= 0 or not NBS_CACHE_PATH.exists():
        return None
    try:
        age_seconds = time.time() - NBS_CACHE_PATH.stat().st_mtime
        if age_seconds > cache_ttl_seconds:
            return None
        with NBS_CACHE_PATH.open("r", encoding="utf-8") as cache_file:
            payload = json.load(cache_file)
        items = payload.get("returndata", {}).get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("缓存不包含有效指标")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("缓存指标结构无效")
            _validate_indicator_item(item)
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("国家统计局缓存读取失败，将重新抓取：%s", type(exc).__name__)
        return None


def _write_cache(payload: dict[str, Any]) -> None:
    temp_path: str | None = None
    try:
        NBS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=NBS_CACHE_PATH.parent,
            prefix=f".{NBS_CACHE_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as cache_file:
            temp_path = cache_file.name
            json.dump(payload, cache_file, ensure_ascii=False, indent=2)
            cache_file.flush()
            os.fsync(cache_file.fileno())
        os.replace(temp_path, NBS_CACHE_PATH)
        temp_path = None
    except OSError as exc:
        logger.warning("国家统计局缓存写入失败：%s", type(exc).__name__)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _request_html(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
    max_retries: int,
) -> str:
    if not _is_allowed_release_url(url):
        raise NBSDataError("国家统计局数据 URL 不受信任")

    last_error: Exception | None = None
    for attempt in range(1, max(1, max_retries) + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            if not response.content:
                raise NBSDataError("国家统计局页面响应为空")
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except (requests.RequestException, NBSDataError) as exc:
            last_error = exc
            logger.warning(
                "国家统计局页面请求失败，第 %s/%s 次：%s",
                attempt,
                max(1, max_retries),
                type(exc).__name__,
            )
            if attempt < max(1, max_retries):
                time.sleep(DEFAULT_RETRY_DELAY_SECONDS * attempt)

    raise NBSDataError(
        f"国家统计局页面请求失败，已重试 {max(1, max_retries)} 次："
        f"{type(last_error).__name__ if last_error else 'UnknownError'}"
    ) from last_error


def _article_title(soup: BeautifulSoup) -> str:
    heading = soup.find("h1")
    if heading:
        heading_text = _normalize_text(heading.get_text(" ", strip=True))
        if heading_text:
            return heading_text
    if soup.title:
        return _normalize_text(
            re.sub(r"\s*-\s*国家统计局\s*$", "", soup.title.get_text(" ", strip=True))
        )
    return ""


def _extract_period(period_type: str, title: str, text: str) -> str:
    if period_type == "monthly":
        match = re.search(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月份?", title)
        if not match:
            match = re.search(
                r"(?P<year>20\d{2})年(?P<month>\d{1,2})月份?",
                text,
            )
        if not match:
            raise NBSDataError("国家统计局文章未提取到月度统计期")
        return f"{match.group('year')}-{int(match.group('month')):02d}"

    match = re.search(
        r"(?P<year>20\d{2})年1\s*[—－-]\s*(?P<month>\d{1,2})月份?",
        title,
    )
    if not match:
        match = re.search(
            r"(?P<year>20\d{2})年1\s*[—－-]\s*(?P<month>\d{1,2})月份?",
            text,
        )
    if not match:
        raise NBSDataError("国家统计局文章未提取到累计统计期")
    return f"{match.group('year')}-01至{match.group('year')}-{int(match.group('month')):02d}"


def _extract_published_at(text: str) -> str:
    match = re.search(
        r"(?P<year>20\d{2})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})"
        r"(?:\s+\d{1,2}:\d{2})?",
        text,
    )
    if not match:
        raise NBSDataError("国家统计局文章未提取到发布日期")
    return (
        f"{match.group('year')}-{int(match.group('month')):02d}-"
        f"{int(match.group('day')):02d}"
    )


def _is_allowed_release_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == NBS_ALLOWED_HOST
        and (parsed.path == "/sj/zxfb/" or parsed.path.startswith("/sj/zxfb"))
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u3000", " ").replace("\xa0", " ")).strip()
