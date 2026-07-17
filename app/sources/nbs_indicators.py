"""国家统计局官方发布页宏观指标配置。"""


NBS_INDICATORS = {
    "cpi_yoy": {
        "indicator": "CPI同比",
        "unit": "%",
        "frequency": "monthly",
        "comparison": "同比",
        "period_type": "monthly",
        "title_terms": ("居民消费价格", "同比"),
        "value_pattern": (
            r"全国居民消费价格同比(?P<direction>上涨|下降)"
            r"(?P<value>\d+(?:\.\d+)?)%"
        ),
    },
    "ppi_yoy": {
        "indicator": "PPI同比",
        "unit": "%",
        "frequency": "monthly",
        "comparison": "同比",
        "period_type": "monthly",
        "title_terms": ("工业生产者出厂价格", "同比"),
        "value_pattern": (
            r"全国工业生产者出厂价格同比(?P<direction>上涨|下降)"
            r"(?P<value>\d+(?:\.\d+)?)%"
        ),
    },
    "industrial_value_added_yoy": {
        "indicator": "规模以上工业增加值同比",
        "unit": "%",
        "frequency": "monthly",
        "comparison": "同比",
        "period_type": "monthly",
        "title_terms": ("规模以上工业增加值",),
        "value_pattern": (
            r"规模以上工业增加值同比实际(?P<direction>增长|下降)"
            r"(?P<value>\d+(?:\.\d+)?)%"
        ),
    },
    "retail_sales_yoy": {
        "indicator": "社会消费品零售总额同比",
        "unit": "%",
        "frequency": "monthly",
        "comparison": "同比",
        "period_type": "monthly",
        "title_terms": ("社会消费品零售总额",),
        "value_pattern": (
            r"\d{1,2}月份，社会消费品零售总额\d+(?:\.\d+)?亿元，"
            r"同比(?P<direction>增长|下降)(?P<value>\d+(?:\.\d+)?)%"
        ),
    },
    "fixed_asset_investment_ytd": {
        "indicator": "固定资产投资累计同比",
        "unit": "%",
        "frequency": "monthly",
        "comparison": "累计同比",
        "period_type": "cumulative_monthly",
        "title_terms": ("全国固定资产投资基本情况",),
        "value_pattern": (
            r"1\s*[—－-]\s*\d{1,2}月份，全国固定资产投资（不含农户）"
            r"\d+(?:\.\d+)?亿元，同比(?P<direction>增长|下降)"
            r"(?P<value>\d+(?:\.\d+)?)%"
        ),
    },
}


def get_indicator(key: str) -> dict:
    """获取国家统计局指标配置。"""
    if key not in NBS_INDICATORS:
        raise KeyError(f"未知国家统计局指标: {key}")
    return NBS_INDICATORS[key]
