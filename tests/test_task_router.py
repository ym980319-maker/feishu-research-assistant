from __future__ import annotations

import unittest

from app.router.task_router import (
    FUND_ANALYSIS,
    GENERAL_CHAT,
    REPORT_ANALYSIS,
    RESEARCH_REPORT,
    SENTIMENT_ANALYSIS,
    route_task,
)


class TaskRouterTests(unittest.TestCase):
    def test_report_analysis(self) -> None:
        self.assertEqual(route_task("分析这份研报"), REPORT_ANALYSIS)

    def test_fund_analysis(self) -> None:
        self.assertEqual(route_task("生成基金投决意见"), FUND_ANALYSIS)

    def test_research_report(self) -> None:
        self.assertEqual(route_task("写一篇信用债专题报告"), RESEARCH_REPORT)

    def test_sentiment_analysis(self) -> None:
        self.assertEqual(route_task("最近城投有什么新闻"), SENTIMENT_ANALYSIS)

    def test_natural_language_examples(self) -> None:
        self.assertEqual(route_task("分析这篇研报"), REPORT_ANALYSIS)
        self.assertEqual(route_task("写一篇关于城投债的专题"), RESEARCH_REPORT)
        self.assertEqual(route_task("分析这个基金值不值得投"), FUND_ANALYSIS)
        self.assertEqual(
            route_task("整理一下最近关于地产债的消息"),
            SENTIMENT_ANALYSIS,
        )

    def test_unknown_or_empty_input_uses_general_chat(self) -> None:
        self.assertEqual(route_task("债券久期是什么意思"), GENERAL_CHAT)
        self.assertEqual(route_task("  "), GENERAL_CHAT)


if __name__ == "__main__":
    unittest.main()

