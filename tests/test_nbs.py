"""国家统计局数据接口测试"""

import json

from app.sources.nbs_source import fetch_nbs_data


def main():

    print("开始请求国家统计局数据...")

    result = fetch_nbs_data(
        dbcode="hgyd",
        rowcode="zb",
        colcode="sj",
    )

    print("\n接口返回成功")
    print("=" * 50)

    print(json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )[:3000])


if __name__ == "__main__":
    main()
