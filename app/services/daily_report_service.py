"""Optional daily-report handler.

The stable daily-report implementation remains in ``app.main``.  This wrapper
keeps the message entry independent from that implementation and avoids a
module import cycle by accepting it as a dependency.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable


DailyReportHandler = Callable[[str], Awaitable[str]]


async def handle_daily_report(
    message: str,
    daily_report_handler: DailyReportHandler,
) -> str:
    return await daily_report_handler(message)

