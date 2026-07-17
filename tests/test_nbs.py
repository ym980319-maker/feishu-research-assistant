"""国家统计局官方数据发布页测试。"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from app.sources.nbs_source import (
    NBS_RELEASE_INDEX_URL,
    NBSDataError,
    fetch_nbs_data,
    parse_indicator_article,
    parse_release_index,
)


INDEX_HTML = """
<html><body>
  <a href="/sj/zxfb/202607/cpi.html">2026年6月份居民消费价格同比上涨1.0%</a>
  <a href="/sj/zxfb/202607/ppi.html">2026年6月份工业生产者出厂价格同比上涨4.1% 环比下降0.3%</a>
  <a href="/sj/zxfb/202607/industrial.html">2026年6月份规模以上工业增加值增长5.3%</a>
  <a href="/sj/zxfb/202607/retail.html">2026年上半年社会消费品零售总额增长1.3%</a>
  <a href="/sj/zxfb/202607/investment.html">2026年1—6月份全国固定资产投资基本情况</a>
</body></html>
"""

ARTICLE_FIXTURES = {
    "cpi_yoy": """
      <html><head><title>2026年6月份居民消费价格同比上涨1.0% - 国家统计局</title></head>
      <body><h1>2026年6月份居民消费价格同比上涨1.0%</h1>
      <div>2026/07/09 09:30</div><p>6月份，全国居民消费价格同比上涨1.0%。</p></body></html>
    """,
    "ppi_yoy": """
      <html><head><title>2026年6月份工业生产者出厂价格同比上涨4.1% 环比下降0.3% - 国家统计局</title></head>
      <body><h1>2026年6月份工业生产者出厂价格同比上涨4.1% 环比下降0.3%</h1>
      <div>2026/07/09 09:30</div><p>6月份，全国工业生产者出厂价格同比上涨4.1%，环比下降0.3%。</p></body></html>
    """,
    "industrial_value_added_yoy": """
      <html><head><title>2026年6月份规模以上工业增加值增长5.3% - 国家统计局</title></head>
      <body><h1>2026年6月份规模以上工业增加值增长5.3%</h1>
      <div>2026/07/15 10:00</div><p>6月份，规模以上工业增加值同比实际增长5.3%。</p></body></html>
    """,
    "retail_sales_yoy": """
      <html><head><title>2026年上半年社会消费品零售总额增长1.3% - 国家统计局</title></head>
      <body><h1>2026年上半年社会消费品零售总额增长1.3%</h1>
      <div>2026/07/15 10:00</div><p>2026年6月份社会消费品零售总额主要数据</p>
      <p>6月份，社会消费品零售总额42691亿元，同比增长1.0%。</p></body></html>
    """,
    "fixed_asset_investment_ytd": """
      <html><head><title>2026年1—6月份全国固定资产投资基本情况 - 国家统计局</title></head>
      <body><h1>2026年1—6月份全国固定资产投资基本情况</h1>
      <div>2026/07/15 10:00</div><p>1—6月份，全国固定资产投资（不含农户）226370亿元，同比下降5.7%。</p></body></html>
    """,
}


class OfflineParsingTests(unittest.TestCase):
    def test_release_index_locates_all_supported_articles(self) -> None:
        articles = parse_release_index(INDEX_HTML)
        self.assertEqual(len(articles), 5)
        self.assertTrue(all(url.startswith("https://www.stats.gov.cn/") for url in articles.values()))

    def test_fixed_html_articles_are_parsed_strictly(self) -> None:
        articles = parse_release_index(INDEX_HTML)
        expected_values = {
            "cpi_yoy": 1.0,
            "ppi_yoy": 4.1,
            "industrial_value_added_yoy": 5.3,
            "retail_sales_yoy": 1.0,
            "fixed_asset_investment_ytd": -5.7,
        }
        for indicator_key, expected_value in expected_values.items():
            with self.subTest(indicator_key=indicator_key):
                record = parse_indicator_article(
                    indicator_key,
                    ARTICLE_FIXTURES[indicator_key],
                    articles[indicator_key],
                )
                self.assertEqual(record["value"], expected_value)
                self.assertEqual(record["source"], "国家统计局")
                self.assertEqual(record["unit"], "%")
                self.assertTrue(record["published_at"].startswith("2026-07-"))

    def test_standardized_item_fields_and_numeric_value(self) -> None:
        source_url = parse_release_index(INDEX_HTML)["cpi_yoy"]
        item = parse_indicator_article("cpi_yoy", ARTICLE_FIXTURES["cpi_yoy"], source_url)
        self.assertEqual(
            set(item),
            {
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
            },
        )
        self.assertIsInstance(item["value"], (int, float))
        self.assertNotIsInstance(item["value"], bool)
        self.assertEqual(item["frequency"], "monthly")
        self.assertEqual(item["comparison"], "同比")


class FetchAndCacheTests(unittest.TestCase):
    def _responses(self, replacements: dict[str, str] | None = None) -> list[str]:
        fixtures = dict(ARTICLE_FIXTURES)
        fixtures.update(replacements or {})
        return [INDEX_HTML, *fixtures.values()]

    def _fetch(self, **kwargs):
        return fetch_nbs_data(
            dbcode="hgyd",
            rowcode="zb",
            colcode="sj",
            max_retries=1,
            use_cache=False,
            **kwargs,
        )

    @patch("app.sources.nbs_source._request_html")
    def test_all_five_indicators_succeed(self, request_html) -> None:
        request_html.side_effect = self._responses()
        payload = self._fetch()
        data = payload["returndata"]
        self.assertEqual(data["success_count"], 5)
        self.assertEqual(data["failure_count"], 0)
        self.assertEqual(data["errors"], [])
        self.assertIs(data["items"], data["records"])

    @patch("app.sources.nbs_source._request_html")
    def test_one_indicator_failure_returns_other_items(self, request_html) -> None:
        broken_retail = ARTICLE_FIXTURES["retail_sales_yoy"].replace("同比增长1.0%", "数据待发布")
        request_html.side_effect = self._responses({"retail_sales_yoy": broken_retail})
        data = self._fetch()["returndata"]
        self.assertEqual(data["success_count"], 4)
        self.assertEqual(data["failure_count"], 1)
        self.assertEqual(data["errors"][0]["indicator_key"], "retail_sales_yoy")
        self.assertNotIn("retail_sales_yoy", {item["indicator_key"] for item in data["items"]})

    @patch("app.sources.nbs_source._request_html", return_value="<html><body></body></html>")
    def test_all_indicators_failure_raises(self, _request_html) -> None:
        with self.assertRaises(NBSDataError):
            self._fetch()

    def test_cache_hit_skips_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "nbs.json"
            with patch("app.sources.nbs_source.NBS_CACHE_PATH", cache_path):
                with patch("app.sources.nbs_source._request_html", side_effect=self._responses()):
                    first = fetch_nbs_data(dbcode="hgyd", rowcode="zb", colcode="sj")
                with patch("app.sources.nbs_source._request_html") as request_html:
                    second = fetch_nbs_data(dbcode="hgyd", rowcode="zb", colcode="sj")
                request_html.assert_not_called()
                self.assertEqual(second, first)

    def test_expired_cache_refetches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "nbs.json"
            with patch("app.sources.nbs_source.NBS_CACHE_PATH", cache_path):
                with patch("app.sources.nbs_source._request_html", side_effect=self._responses()):
                    fetch_nbs_data(dbcode="hgyd", rowcode="zb", colcode="sj")
                old_time = time.time() - 60
                os.utime(cache_path, (old_time, old_time))
                with patch("app.sources.nbs_source._request_html", side_effect=self._responses()) as request_html:
                    fetch_nbs_data(
                        dbcode="hgyd", rowcode="zb", colcode="sj", cache_ttl_seconds=1
                    )
                self.assertEqual(request_html.call_count, 6)

    def test_corrupt_cache_refetches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "nbs.json"
            cache_path.write_text("{not-json", encoding="utf-8")
            with patch("app.sources.nbs_source.NBS_CACHE_PATH", cache_path):
                with patch("app.sources.nbs_source._request_html", side_effect=self._responses()) as request_html:
                    payload = fetch_nbs_data(dbcode="hgyd", rowcode="zb", colcode="sj")
                self.assertEqual(request_html.call_count, 6)
                self.assertEqual(payload["returndata"]["success_count"], 5)
                with cache_path.open("r", encoding="utf-8") as cache_file:
                    self.assertEqual(json.load(cache_file)["returncode"], 200)


class OnlineReleaseTests(unittest.TestCase):
    def test_release_index_is_accessible(self) -> None:
        response = requests.get(NBS_RELEASE_INDEX_URL, timeout=15)
        response.raise_for_status()
        self.assertTrue(response.content)

    def test_fetch_extracts_at_least_cpi_and_ppi(self) -> None:
        payload = fetch_nbs_data(
            dbcode="hgyd",
            rowcode="zb",
            colcode="sj",
            timeout=15,
            max_retries=2,
            use_cache=False,
        )
        records = payload["returndata"]["items"]
        by_key = {record["indicator_key"]: record for record in records}
        self.assertIn("cpi_yoy", by_key)
        self.assertIn("ppi_yoy", by_key)
        self.assertIsInstance(by_key["cpi_yoy"]["value"], float)
        self.assertIsInstance(by_key["ppi_yoy"]["value"], float)


if __name__ == "__main__":
    unittest.main(verbosity=2)
