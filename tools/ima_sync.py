"""Windows ima 知识库同步工具。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.sources.ima_parser import parse_ima_home_response


def load_json(path: Path) -> dict[str, Any]:
    """读取 ima 导出的 JSON 文件。"""

    if not path.exists():
        raise FileNotFoundError(f"没有找到文件：{path}")

    if not path.is_file():
        raise ValueError(f"不是有效文件：{path}")

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError("JSON 顶层必须是对象")

    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="读取并解析 ima 知识库 JSON"
    )
    parser.add_argument(
        "json_file",
        type=Path,
        help="ima 接口响应 JSON 文件路径",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = load_json(args.json_file)
    page = parse_ima_home_response(payload)

    print("=" * 60)
    print("知识库：", page.knowledge_base.name)
    print("总条数：", page.knowledge_base.knowledge_total_size)
    print("本次返回：", len(page.items))
    print("是否结束：", page.is_end)
    print("下一页标识：", page.next_cursor or "无")
    print("=" * 60)

    for index, item in enumerate(page.items, start=1):
        item_type = item.media_type_name or str(item.media_type)
        title = item.title or item.media_id

        print(f"{index:02d}. [{item_type}] {title}")


if __name__ == "__main__":
    main()
