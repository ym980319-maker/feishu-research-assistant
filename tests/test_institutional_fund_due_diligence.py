from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from app.services.fund_analysis_service import handle_fund_analysis


INSTITUTIONAL_PRODUCT_TEXT = """
一、产品基本信息
产品名称：示例稳健增强产品
管理人：示例资产管理有限公司
产品类型：混合债券型产品
成立时间：2024-01-15
产品规模：材料未披露
开放安排：每季度开放一次

二、投资策略
主要投资高等级信用债，并通过利率债久期调整获取收益。

三、投资范围
可投资利率债、信用债及可转债，不投资股票。

四、风险因素
面临利率波动、信用利差走阔及开放期赎回风险。

五、管理团队
由固定收益投资团队共同管理。
"""


class InstitutionalFundDueDiligenceTests(unittest.IsolatedAsyncioTestCase):
    async def _generate(self):
        model = AsyncMock(return_value="产品尽调分析报告正文")
        result = await handle_fund_analysis(
            "分析示例稳健增强产品",
            model,
            AsyncMock(return_value="机构产品评价框架"),
            AsyncMock(return_value={}),
            documents=INSTITUTIONAL_PRODUCT_TEXT,
        )
        return result, model

    async def test_uses_institutional_due_diligence_template(self) -> None:
        result, model = await self._generate()

        self.assertEqual(result, "产品尽调分析报告正文")
        model.assert_awaited_once()
        prompt, task_type = model.await_args.args
        self.assertEqual(task_type, "基金产品研究")
        for section in (
            "# 产品尽调分析报告",
            "## 一、产品基本信息",
            "## 二、产品定位与投资逻辑",
            "## 三、投资策略拆解",
            "## 四、历史表现与风险指标",
            "## 五、资产配置与组合价值",
            "## 六、风险分析",
            "## 七、管理人与团队分析",
            "## 八、投资价值判断",
        ):
            self.assertIn(section, prompt)

    async def test_prompt_contains_institutional_analysis_dimensions(self) -> None:
        _, model = await self._generate()
        prompt = model.await_args.args[0]

        for required in (
            "这个产品靠什么赚钱",
            "久期收益",
            "信用利差",
            "转债增强",
            "仓位管理",
            "久期管理",
            "信用筛选",
            "年化收益",
            "最大回撤",
            "波动率",
            "夏普比率",
            "收益稳定性",
            "固收增强",
            "另类收益",
            "权益替代",
            "流动性管理",
            "### 市场风险",
            "### 信用风险",
            "### 流动性风险",
            "### 策略风险",
            "### 配置价值",
            "### 主要风险",
            "### 建议进一步核查事项",
        ):
            self.assertIn(required, prompt)

    async def test_missing_metrics_must_use_exact_disclosure_statement(self) -> None:
        _, model = await self._generate()
        prompt = model.await_args.args[0]

        self.assertIn("材料未披露，无法判断。", prompt)
        self.assertIn("不得利用常识估算", prompt)
        self.assertIn("不得推定产品具备相关能力", prompt)

    async def test_structured_document_and_final_report_share_one_kimi_call(self) -> None:
        _, model = await self._generate()
        prompt = model.await_args.args[0]

        model.assert_awaited_once()
        self.assertIn('"产品信息":', prompt)
        self.assertIn("示例稳健增强产品", prompt)
        self.assertIn('"投资策略":', prompt)
        self.assertIn("高等级信用债", prompt)
        self.assertIn('"风险因素":', prompt)


if __name__ == "__main__":
    unittest.main()
