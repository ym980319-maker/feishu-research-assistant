from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.providers.nbs_macro import (
    INDICATOR_SPECS,
    NBS_ALLOWED_HOSTS,
    NbsMacroProviderError,
    fetch_latest_nbs_macro_data,
    parse_nbs_macro_payload,
    select_latest_nbs_macro_data,
)


LABELS = {
    "A01010101": "居民消费价格指数(上年同月=100)",
    "A010801": "工业生产者出厂价格指数(上年同月=100)",
    "A0B0101": "制造业采购经理指数",
}


def payload(
    code: str,
    values: list[tuple[str, object]],
    *,
    label: str | None = None,
    published_at: str | None = "2026-07-09",
) -> dict:
    result = {
        "returncode": 200,
        "returndata": {
            "datanodes": [
                {
                    "data": {"data": value, "hasdata": True},
                    "wds": [
                        {"wdcode": "zb", "valuecode": code},
                        {"wdcode": "sj", "valuecode": period},
                    ],
                }
                for period, value in values
            ],
            "wdnodes": [
                {
                    "wdcode": "zb",
                    "nodes": [{"code": code, "name": label or LABELS[code]}],
                }
            ],
        },
    }
    if published_at:
        result["published_at"] = published_at
    return result


def response(value: dict | None = None, *, content: bytes | None = None) -> MagicMock:
    item = MagicMock()
    item.content = content if content is not None else b"{}"
    item.json.return_value = value
    return item


def client_context(*responses: MagicMock):
    client = AsyncMock()
    client.get.side_effect = list(responses)
    context = AsyncMock()
    context.__aenter__.return_value = client
    return context, client


class NbsMacroParsingTests(unittest.TestCase):
    def test_cpi_month_on_year_is_percentage_change(self) -> None:
        records = parse_nbs_macro_payload(payload("A01010101", [("202606", 100.1)]))
        self.assertEqual(records[0]["indicator"], "CPI")
        self.assertAlmostEqual(records[0]["value"], 0.1)
        self.assertEqual(records[0]["unit"], "%")

    def test_ppi_month_on_year_is_percentage_change(self) -> None:
        records = parse_nbs_macro_payload(payload("A010801", [("202606", "97.9")]))
        self.assertEqual(records[0]["indicator"], "PPI")
        self.assertAlmostEqual(records[0]["value"], -2.1)
        self.assertEqual(records[0]["unit"], "%")

    def test_pmi_is_index_points_and_percent_text_is_not_scaled(self) -> None:
        records = parse_nbs_macro_payload(payload("A0B0101", [("202606", "50.3%")] ))
        self.assertEqual(records[0]["value"], 50.3)
        self.assertEqual(records[0]["unit"], "指数点")

    def test_period_and_publication_date_are_distinct(self) -> None:
        records = parse_nbs_macro_payload(
            payload("A01010101", [("202606", 100.1)], published_at="2026-07-09")
        )
        self.assertEqual(records[0]["period"], "2026-06")
        self.assertEqual(records[0]["published_at"], "2026-07-09")

        without_publication = parse_nbs_macro_payload(
            payload("A01010101", [("202606", 100.1)], published_at=None)
        )
        self.assertIsNone(without_publication[0]["published_at"])

    def test_latest_period_is_selected(self) -> None:
        records = parse_nbs_macro_payload(
            payload("A0B0101", [("202605", 49.5), ("202606", 50.3)])
        )
        selected = select_latest_nbs_macro_data(records)
        self.assertEqual(selected[0]["period"], "2026-06")
        self.assertEqual(selected[0]["value"], 50.3)

    def test_duplicate_indicator_period_is_removed(self) -> None:
        records = parse_nbs_macro_payload(
            payload("A010801", [("202606", 97.9), ("202606", 97.9)])
        )
        selected = select_latest_nbs_macro_data(records)
        self.assertEqual(len(selected), 1)

    def test_missing_indicator_keeps_other_records_and_is_explicit(self) -> None:
        records = []
        records.extend(parse_nbs_macro_payload(payload("A01010101", [("202606", 100.1)])))
        records.extend(parse_nbs_macro_payload(payload("A0B0101", [("202606", 50.3)])))
        selected = select_latest_nbs_macro_data(records)
        self.assertEqual([item["indicator"] for item in selected], ["CPI", "PMI"])
        self.assertEqual(selected.missing_indicators, ("PPI",))

    def test_wrong_measure_labels_are_rejected(self) -> None:
        wrong_labels = (
            ("A01010101", "居民消费价格指数(上月=100)"),
            ("A01010101", "居民消费价格指数(上年同期=100)"),
            ("A01010101", "核心居民消费价格指数(上年同月=100)"),
            ("A010801", "工业生产者出厂价格指数(上月=100)"),
            ("A010801", "工业生产者出厂价格指数(上年同期=100)"),
            ("A0B0101", "非制造业采购经理指数"),
            ("A0B0101", "综合PMI产出指数"),
            ("A0B0101", "制造业生产指数"),
        )
        for code, label in wrong_labels:
            with self.subTest(code=code, label=label):
                self.assertEqual(
                    parse_nbs_macro_payload(payload(code, [("202606", 100)], label=label)),
                    [],
                )

    def test_missing_or_changed_fields_do_not_create_data(self) -> None:
        invalid_payloads = (
            {},
            {"returncode": 200, "returndata": {}},
            {"returncode": 200, "returndata": {"datanodes": "changed"}},
            {
                "returncode": 200,
                "returndata": {"datanodes": [{"data": {"data": 100.1}}]},
            },
        )
        for value in invalid_payloads:
            with self.subTest(value=value):
                self.assertEqual(parse_nbs_macro_payload(value), [])

    def test_all_official_urls_use_nbs_hosts(self) -> None:
        records = []
        for spec in INDICATOR_SPECS:
            raw_value = 50.3 if spec.indicator == "PMI" else 100.1
            records.extend(parse_nbs_macro_payload(payload(spec.code, [("202606", raw_value)])))
        self.assertTrue(records)
        for item in records:
            parsed = urlsplit(item["official_url"])
            self.assertEqual(parsed.scheme, "https")
            self.assertIn(parsed.hostname, NBS_ALLOWED_HOSTS)
            self.assertEqual(item["source"], "国家统计局")


class NbsMacroNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_uses_mocked_official_responses(self) -> None:
        responses = [
            response(payload("A01010101", [("202606", 100.1)])),
            response(payload("A010801", [("202606", 97.9)])),
            response(payload("A0B0101", [("202606", 50.3)])),
        ]
        context, client = client_context(*responses)
        with patch("app.providers.nbs_macro.httpx.AsyncClient", return_value=context):
            records = await fetch_latest_nbs_macro_data()

        self.assertEqual([item["indicator"] for item in records], ["CPI", "PPI", "PMI"])
        self.assertEqual(records.missing_indicators, ())
        self.assertEqual(client.get.await_count, 3)
        for call in client.get.await_args_list:
            params = call.kwargs["params"]
            filters = json.loads(params["dfwds"])
            self.assertIn(filters[0]["valuecode"], LABELS)
            self.assertEqual(urlsplit(call.args[0]).hostname, "data.stats.gov.cn")

    async def test_http_error_is_wrapped_without_response_body(self) -> None:
        failed = response()
        failed.raise_for_status.side_effect = httpx.HTTPStatusError(
            "body contains sensitive diagnostics",
            request=httpx.Request("GET", "https://data.stats.gov.cn/easyquery.htm"),
            response=httpx.Response(503),
        )
        context, _ = client_context(failed)
        with patch("app.providers.nbs_macro.httpx.AsyncClient", return_value=context):
            with self.assertRaises(NbsMacroProviderError) as raised:
                await fetch_latest_nbs_macro_data()

        self.assertIn("HTTPStatusError", str(raised.exception))
        self.assertNotIn("sensitive diagnostics", str(raised.exception))

    async def test_non_json_response_raises_provider_error(self) -> None:
        invalid = response(content=b"<html>not json</html>")
        invalid.json.side_effect = ValueError("not json")
        context, _ = client_context(invalid)
        with patch("app.providers.nbs_macro.httpx.AsyncClient", return_value=context):
            with self.assertRaisesRegex(NbsMacroProviderError, "non-JSON"):
                await fetch_latest_nbs_macro_data()

    async def test_empty_response_raises_provider_error(self) -> None:
        context, _ = client_context(response(content=b""))
        with patch("app.providers.nbs_macro.httpx.AsyncClient", return_value=context):
            with self.assertRaisesRegex(NbsMacroProviderError, "empty response"):
                await fetch_latest_nbs_macro_data()

    async def test_one_missing_indicator_keeps_other_results(self) -> None:
        responses = [
            response(payload("A01010101", [("202606", 100.1)])),
            response({"returncode": 200, "returndata": {"datanodes": [], "wdnodes": []}}),
            response(payload("A0B0101", [("202606", 50.3)])),
        ]
        context, _ = client_context(*responses)
        with patch("app.providers.nbs_macro.httpx.AsyncClient", return_value=context):
            records = await fetch_latest_nbs_macro_data()

        self.assertEqual([item["indicator"] for item in records], ["CPI", "PMI"])
        self.assertEqual(records.missing_indicators, ("PPI",))


class NbsMacroIsolationTests(unittest.TestCase):
    def test_existing_registry_and_business_entrypoint_do_not_reference_nbs(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = (root / "app" / "providers" / "registry.py").read_text(encoding="utf-8")
        main = (root / "app" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("nbs_macro", registry)
        self.assertNotIn("NbsMacro", registry)
        self.assertNotIn("nbs_macro", main)
        self.assertNotIn("NbsMacro", main)


if __name__ == "__main__":
    unittest.main()
