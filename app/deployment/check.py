"""Command-line production environment validation.

The check reports configuration variable names only. It never includes the
configured values, which keeps startup output safe for shared deployment logs.
"""

from __future__ import annotations

from dataclasses import dataclass
import sys

from app.config import AppConfig, load_config


@dataclass(frozen=True, slots=True)
class EnvironmentCheckResult:
    """Secret-safe result returned by :func:`check_environment`."""

    ready: bool
    missing: tuple[str, ...]
    message: str


def check_environment(
    config: AppConfig | None = None,
    *,
    load_dotenv_file: bool = True,
) -> EnvironmentCheckResult:
    """Validate production integrations without making external requests."""
    selected = config or load_config(load_dotenv_file=load_dotenv_file)
    required = {
        "FEISHU_APP_ID": selected.feishu.app_id,
        "FEISHU_APP_SECRET": selected.feishu.app_secret,
        "KIMI_API_KEY": selected.kimi.api_key,
        "DEEPSEEK_API_KEY": selected.deepseek.api_key,
        "TAVILY_API_KEY": selected.tavily.api_key,
    }
    missing = tuple(name for name, value in required.items() if not value)
    if missing:
        return EnvironmentCheckResult(
            ready=False,
            missing=missing,
            message="生产环境检查未通过，缺少配置：" + ", ".join(missing),
        )
    return EnvironmentCheckResult(
        ready=True,
        missing=(),
        message="生产环境检查通过：飞书、Kimi、DeepSeek、Tavily 配置已就绪。",
    )


def main() -> int:
    result = check_environment()
    stream = sys.stdout if result.ready else sys.stderr
    print(result.message, file=stream)
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
