from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx


NBS_SOURCE = "国家统计局"
NBS_API_URL = "https://data.stats.gov.cn/easyquery.htm"
NBS_ALLOWED_HOSTS = frozenset({"data.stats.gov.cn", "www.stats.gov.cn"})
NBS_REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class NbsMacroProviderError(RuntimeError):
    """A safe, response-body-free error raised by the NBS macro provider."""


class NbsMacroData(list[dict[str, Any]]):
    """List-compatible result carrying indicators absent from the official response."""

    def __init__(
        self,
        records: Iterable[dict[str, Any]] = (),
        *,
        missing_indicators: Iterable[str] = (),
    ) -> None:
        super().__init__(records)
        self.missing_indicators = tuple(missing_indicators)


@dataclass(frozen=True)
class _IndicatorSpec:
    indicator: str
    code: str
    unit: str
    labels: frozenset[str]

    @property
    def official_url(self) -> str:
        return f"https://data.stats.gov.cn/easyquery.htm?cn=A01&zb={self.code}"


INDICATOR_SPECS = (
    _IndicatorSpec(
        indicator="CPI",
        code="A01010101",
        unit="%",
        labels=frozenset(
            {
                "居民消费价格指数(上年同月=100)",
                "全国居民消费价格指数(上年同月=100)",
            }
        ),
    ),
    _IndicatorSpec(
        indicator="PPI",
        code="A010801",
        unit="%",
        labels=frozenset({"工业生产者出厂价格指数(上年同月=100)"}),
    ),
    _IndicatorSpec(
        indicator="PMI",
        code="A0B0101",
        unit="指数点",
        labels=frozenset({"制造业采购经理指数"}),
    ),
)


def _normalize_label(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", "", text.translate(str.maketrans("（）＝", "()=")))


def _normalize_period(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{4})(?:年|-)?(0?[1-9]|1[0-2])(?:月)?", text)
    if not match:
        return None
    return f"{match.group(1)}-{int(match.group(2)):02d}"


def _normalize_published_at(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T\s].*)?$", text)
    return match.group(1) if match else None


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _dimension_value(node: dict[str, Any], wdcode: str) -> str:
    dimensions = node.get("wds")
    if not isinstance(dimensions, list):
        return ""
    for dimension in dimensions:
        if isinstance(dimension, dict) and dimension.get("wdcode") == wdcode:
            return str(dimension.get("valuecode") or "").strip()
    return ""


def _label_map(payload: dict[str, Any]) -> dict[str, str]:
    returndata = payload.get("returndata")
    if not isinstance(returndata, dict):
        return {}
    dimensions = returndata.get("wdnodes")
    if not isinstance(dimensions, list):
        return {}
    for dimension in dimensions:
        if not isinstance(dimension, dict) or dimension.get("wdcode") != "zb":
            continue
        nodes = dimension.get("nodes")
        if not isinstance(nodes, list):
            return {}
        return {
            str(node.get("code") or "").strip(): str(node.get("name") or "").strip()
            for node in nodes
            if isinstance(node, dict)
        }
    return {}


def _published_at(payload: dict[str, Any], node: dict[str, Any]) -> str | None:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    returndata = payload.get("returndata")
    metadata = returndata if isinstance(returndata, dict) else {}
    for container in (node, data, metadata, payload):
        for key in ("published_at", "publication_date", "release_date"):
            published = _normalize_published_at(container.get(key))
            if published:
                return published
    return None


def parse_nbs_macro_payload(
    payload: dict[str, Any], expected: _IndicatorSpec | None = None
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    return_code = payload.get("returncode")
    if return_code not in (None, 200, "200"):
        raise NbsMacroProviderError("NBS API returned an unsuccessful code")
    returndata = payload.get("returndata")
    if not isinstance(returndata, dict):
        return []
    nodes = returndata.get("datanodes")
    if not isinstance(nodes, list):
        return []

    labels = _label_map(payload)
    specs = (expected,) if expected else INDICATOR_SPECS
    by_code = {spec.code: spec for spec in specs if spec is not None}
    allowed_labels = {
        spec.code: {_normalize_label(label) for label in spec.labels}
        for spec in specs
        if spec is not None
    }
    records: list[dict[str, Any]] = []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        code = _dimension_value(node, "zb")
        period = _normalize_period(_dimension_value(node, "sj"))
        spec = by_code.get(code)
        raw_label = labels.get(code, "")
        if (
            spec is None
            or period is None
            or _normalize_label(raw_label) not in allowed_labels.get(code, set())
        ):
            continue
        data = node.get("data")
        if not isinstance(data, dict) or data.get("hasdata") is False:
            continue
        raw_value = _numeric_value(data.get("data"))
        if raw_value is None:
            continue
        value = raw_value - 100.0 if spec.indicator in {"CPI", "PPI"} else raw_value
        records.append(
            {
                "indicator": spec.indicator,
                "value": round(value, 6),
                "unit": spec.unit,
                "period": period,
                "published_at": _published_at(payload, node),
                "source": NBS_SOURCE,
                "official_url": spec.official_url,
                "verification_status": "content_verified",
                "raw_label": raw_label,
            }
        )
    return records


def select_latest_nbs_macro_data(records: Iterable[dict[str, Any]]) -> NbsMacroData:
    latest: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        indicator = str(record.get("indicator") or "")
        period = _normalize_period(record.get("period"))
        value = record.get("value")
        if indicator not in {spec.indicator for spec in INDICATOR_SPECS}:
            continue
        if period is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        identity = (indicator, period)
        if identity in seen:
            continue
        seen.add(identity)
        normalized = dict(record)
        normalized["period"] = period
        current = latest.get(indicator)
        if current is None or period > current["period"]:
            latest[indicator] = normalized

    ordered = [
        latest[spec.indicator]
        for spec in INDICATOR_SPECS
        if spec.indicator in latest
    ]
    missing = [
        spec.indicator for spec in INDICATOR_SPECS if spec.indicator not in latest
    ]
    return NbsMacroData(ordered, missing_indicators=missing)


def _request_params(spec: _IndicatorSpec) -> dict[str, str]:
    return {
        "m": "QueryData",
        "dbcode": "hgyd",
        "rowcode": "zb",
        "colcode": "sj",
        "wds": "[]",
        "dfwds": json.dumps(
            [{"wdcode": "zb", "valuecode": spec.code}],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


async def fetch_latest_nbs_macro_data() -> NbsMacroData:
    if urlsplit(NBS_API_URL).hostname not in NBS_ALLOWED_HOSTS:
        raise NbsMacroProviderError("NBS API host is not allowed")

    records: list[dict[str, Any]] = []
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://data.stats.gov.cn/easyquery.htm?cn=A01",
        "User-Agent": "feishu-research-assistant/nbs-macro",
    }
    async with httpx.AsyncClient(timeout=NBS_REQUEST_TIMEOUT) as client:
        for spec in INDICATOR_SPECS:
            try:
                response = await client.get(
                    NBS_API_URL,
                    params=_request_params(spec),
                    headers=headers,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise NbsMacroProviderError(
                    f"NBS HTTP request failed for {spec.indicator}: {type(exc).__name__}"
                ) from None
            if not response.content:
                raise NbsMacroProviderError(
                    f"NBS returned an empty response for {spec.indicator}"
                )
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                raise NbsMacroProviderError(
                    f"NBS returned non-JSON data for {spec.indicator}"
                ) from None
            records.extend(parse_nbs_macro_payload(payload, expected=spec))

    return select_latest_nbs_macro_data(records)


__all__ = [
    "INDICATOR_SPECS",
    "NBS_ALLOWED_HOSTS",
    "NBS_API_URL",
    "NbsMacroData",
    "NbsMacroProviderError",
    "fetch_latest_nbs_macro_data",
    "parse_nbs_macro_payload",
    "select_latest_nbs_macro_data",
]
