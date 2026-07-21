"""Central application configuration and environment loading."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv


DEFAULT_FEISHU_TENANT_DOMAIN = "qcn787gcsi1s.feishu.cn"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_KIMI_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_KIMI_MODEL = "kimi-k2.6"
DEFAULT_TAVILY_ENDPOINT = "https://api.tavily.com/search"
DEFAULT_TAVILY_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""
    bitable_app_token: str = ""
    news_table_id: str = ""
    task_table_id: str = ""
    report_table_id: str = ""
    knowledge_table_id: str = ""
    market_table_id: str = ""
    doc_folder_token: str = ""
    tenant_domain: str = DEFAULT_FEISHU_TENANT_DOMAIN


@dataclass(frozen=True, slots=True)
class ModelConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = ""


@dataclass(frozen=True, slots=True)
class TavilyConfig:
    api_key: str = ""
    endpoint: str = DEFAULT_TAVILY_ENDPOINT
    timeout_seconds: float = DEFAULT_TAVILY_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class AppConfig:
    feishu: FeishuConfig
    kimi: ModelConfig
    deepseek: ModelConfig
    tavily: TavilyConfig


def _text(environ: Mapping[str, str], name: str, default: str = "") -> str:
    return str(environ.get(name, default) or "").strip()


def _float(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float = 0.1,
) -> float:
    try:
        value = float(environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def load_config(
    environ: Mapping[str, str] | None = None,
    *,
    load_dotenv_file: bool = True,
) -> AppConfig:
    """Load an immutable configuration snapshot from one environment mapping."""
    if load_dotenv_file:
        load_dotenv()
    source = os.environ if environ is None else environ
    return AppConfig(
        feishu=FeishuConfig(
            app_id=_text(source, "FEISHU_APP_ID"),
            app_secret=_text(source, "FEISHU_APP_SECRET"),
            bitable_app_token=_text(source, "FEISHU_BITABLE_APP_TOKEN"),
            news_table_id=_text(source, "FEISHU_NEWS_TABLE_ID"),
            task_table_id=_text(source, "FEISHU_TASK_TABLE_ID"),
            report_table_id=_text(source, "FEISHU_REPORT_TABLE_ID"),
            knowledge_table_id=_text(source, "FEISHU_KNOWLEDGE_TABLE_ID"),
            market_table_id=_text(source, "FEISHU_MARKET_TABLE_ID"),
            doc_folder_token=_text(source, "FEISHU_DOC_FOLDER_TOKEN"),
            tenant_domain=_text(
                source,
                "FEISHU_TENANT_DOMAIN",
                DEFAULT_FEISHU_TENANT_DOMAIN,
            ),
        ),
        kimi=ModelConfig(
            api_key=_text(source, "KIMI_API_KEY"),
            base_url=_text(source, "KIMI_BASE_URL", DEFAULT_KIMI_BASE_URL),
            model=_text(source, "KIMI_MODEL", DEFAULT_KIMI_MODEL),
        ),
        deepseek=ModelConfig(
            api_key=_text(source, "DEEPSEEK_API_KEY"),
            base_url=_text(
                source,
                "DEEPSEEK_BASE_URL",
                DEFAULT_DEEPSEEK_BASE_URL,
            ),
        ),
        tavily=TavilyConfig(
            api_key=_text(source, "TAVILY_API_KEY"),
            endpoint=_text(source, "TAVILY_SEARCH_URL", DEFAULT_TAVILY_ENDPOINT),
            timeout_seconds=_float(
                source,
                "TAVILY_TIMEOUT_SECONDS",
                DEFAULT_TAVILY_TIMEOUT_SECONDS,
            ),
        ),
    )


def environment_value(name: str, default: str | None = None) -> str | None:
    """Read a raw environment value through the centralized config boundary."""
    return os.environ.get(name, default)


def environment_bool(name: str, default: bool) -> bool:
    value = environment_value(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def environment_int(
    name: str,
    default: int,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    try:
        value = int(environment_value(name, str(default)))
    except (TypeError, ValueError):
        return default
    if value < minimum or (maximum is not None and value > maximum):
        return default
    return value


def environment_float(
    name: str,
    default: float,
    *,
    minimum: float = 0.1,
    maximum: float | None = None,
) -> float:
    try:
        value = float(environment_value(name, str(default)))
    except (TypeError, ValueError):
        return default
    if value < minimum or (maximum is not None and value > maximum):
        return default
    return value
