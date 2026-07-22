"""Standalone long-running Web service entry for the Research Assistant."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.bootstrap import (
    ApplicationFactory,
    ApplicationServices,
    create_app,
    initialize_services,
)
from app.config import AppConfig, load_config
from app.models.request import ResearchRequest
from app.models.response import ResearchResponse
from app.router.task_router import (
    DAILY_REPORT,
    FUND_ANALYSIS,
    GENERAL_CHAT,
    REPORT_ANALYSIS,
    RESEARCH_REPORT,
    SENTIMENT_ANALYSIS,
    route_task,
)


RuntimeHandler = Callable[..., Awaitable[str]]

TASK_TYPE_ALIASES = {
    "sentiment_analysis": SENTIMENT_ANALYSIS,
    "report_analysis": REPORT_ANALYSIS,
    "research_report": RESEARCH_REPORT,
    "deep_research": RESEARCH_REPORT,
    "fund_analysis": FUND_ANALYSIS,
    "fund_investment_decision": FUND_ANALYSIS,
    "general_chat": GENERAL_CHAT,
    "daily_report": DAILY_REPORT,
}


class ResearchRequestError(ValueError):
    """Raised for unsupported standalone service requests."""


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    kimi_handler: RuntimeHandler
    deepseek_handler: RuntimeHandler
    knowledge_provider: RuntimeHandler
    deep_report_handler: RuntimeHandler
    legacy_daily_handler: RuntimeHandler


def load_runtime_dependencies() -> RuntimeDependencies:
    """Load the stable implementations lazily from the compatibility runtime."""
    from app import main as runtime

    return RuntimeDependencies(
        kimi_handler=runtime.call_kimi,
        deepseek_handler=runtime.call_deepseek,
        knowledge_provider=runtime.read_knowledge_records,
        deep_report_handler=runtime.generate_deep_report,
        legacy_daily_handler=runtime.handle_daily_report,
    )


def resolve_task_type(task_type: str, query: str) -> str:
    normalized = str(task_type or "").strip().lower()
    if normalized == "auto":
        return route_task(query)
    try:
        return TASK_TYPE_ALIASES[normalized]
    except KeyError as exc:
        allowed = ", ".join(sorted((*TASK_TYPE_ALIASES, "auto")))
        raise ResearchRequestError(
            f"不支持的 task_type：{task_type}；可选值：{allowed}"
        ) from exc


async def health() -> dict[str, str]:
    """Minimal liveness endpoint for container and process supervisors."""
    return {"status": "ok"}


def create_research_handler(
    services: ApplicationServices,
    runtime: RuntimeDependencies | None = None,
):
    async def research(request: ResearchRequest) -> ResearchResponse:
        selected_runtime = runtime or load_runtime_dependencies()
        routed_task = resolve_task_type(request.task_type, request.query)
        result = await services.research_assistant(
            request.query,
            kimi_handler=selected_runtime.kimi_handler,
            deepseek_handler=selected_runtime.deepseek_handler,
            knowledge_provider=selected_runtime.knowledge_provider,
            deep_report_handler=selected_runtime.deep_report_handler,
            legacy_daily_handler=selected_runtime.legacy_daily_handler,
            evidence_researcher=services.public_search_provider.search,
            routed_task=routed_task,
        )
        research_task_type = result.research_task_type
        return ResearchResponse(
            task_type=result.routed_task,
            query=request.query,
            research_task_type=(
                research_task_type.value if research_task_type is not None else None
            ),
            content=result.content,
        )

    return research


def _as_http_error(error: ResearchRequestError) -> Exception:
    try:
        from fastapi import HTTPException
    except (ImportError, ModuleNotFoundError):
        return error
    return HTTPException(status_code=422, detail=str(error))


def create_server_app(
    *,
    config: AppConfig | None = None,
    services: ApplicationServices | None = None,
    runtime: RuntimeDependencies | None = None,
    application_factory: ApplicationFactory | None = None,
    strict_startup: bool = False,
) -> Any:
    """Build the standalone app through the shared bootstrap layer."""
    selected_services = services or initialize_services(
        config,
        strict_startup=strict_startup,
    )
    application = create_app(
        config=config,
        services=selected_services,
        application_factory=application_factory,
        health_handler=health,
    )
    research_handler = create_research_handler(selected_services, runtime)

    async def research_endpoint(request: ResearchRequest) -> ResearchResponse:
        try:
            return await research_handler(request)
        except ResearchRequestError as exc:
            raise _as_http_error(exc) from exc

    application.post("/research")(research_endpoint)
    return application


def run_server(
    *,
    config: AppConfig | None = None,
    application_factory: ApplicationFactory | None = None,
    uvicorn_runner: Callable[..., Any] | None = None,
) -> None:
    """Initialize production components strictly and run until terminated."""
    selected_config = config or load_config()
    services = initialize_services(selected_config, strict_startup=True)
    application = create_server_app(
        config=selected_config,
        services=services,
        application_factory=application_factory,
    )
    if uvicorn_runner is None:
        try:
            import uvicorn
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "服务启动失败：缺少 uvicorn，请先安装 requirements.txt"
            ) from exc
        uvicorn_runner = uvicorn.run
    uvicorn_runner(
        application,
        host=selected_config.server.host,
        port=selected_config.server.port,
    )


def _build_default_app() -> Any | None:
    try:
        return create_server_app()
    except ModuleNotFoundError as exc:
        if exc.name != "fastapi":
            raise
        return None


app = _build_default_app()


def main() -> None:
    run_server()


if __name__ == "__main__":
    main()
