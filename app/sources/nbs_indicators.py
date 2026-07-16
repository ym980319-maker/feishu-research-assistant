"""国家统计局宏观指标配置。"""

NBS_INDICATORS = {

    # 居民消费价格指数
    "cpi_yoy": {
        "name": "CPI同比",
        "unit": "%",
        "dbcode": "hgyd",
        "description": "居民消费价格指数同比变化",
    },

    # 工业生产者价格指数
    "ppi_yoy": {
        "name": "PPI同比",
        "unit": "%",
        "dbcode": "hgyd",
        "description": "工业生产者出厂价格同比变化",
    },

    # 工业增加值
    "industrial_value_added": {
        "name": "规模以上工业增加值",
        "unit": "%",
        "dbcode": "hgyd",
        "description": "规模以上工业增加值同比增速",
    },

    # 固定资产投资
    "fixed_asset_investment": {
        "name": "固定资产投资",
        "unit": "%",
        "dbcode": "hgyd",
        "description": "全国固定资产投资累计同比增速",
    },

    # 社会消费品零售
    "retail_sales": {
        "name": "社会消费品零售总额",
        "unit": "%",
        "dbcode": "hgyd",
        "description": "社会消费品零售总额同比增速",
    },

    # 房地产投资
    "real_estate_investment": {
        "name": "房地产开发投资",
        "unit": "%",
        "dbcode": "hgyd",
        "description": "房地产开发投资累计同比增速",
    },

}


def get_indicator(key: str) -> dict:
    """获取国家统计局指标配置。"""
    if key not in NBS_INDICATORS:
        raise KeyError(f"未知国家统计局指标: {key}")

    return NBS_INDICATORS[key]
