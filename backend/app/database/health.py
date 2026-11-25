"""Reusable application database health check.

It opens a real session from the pool and runs ``SELECT 1`` through it, which
proves connectivity, query execution and session handling in one step. The
result holds only the outcome, latency and the exception *type*: never the URL,
user name, host or driver message, which can contain any of those.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from sqlalchemy import text

from app.database.session import Database

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    reachable: bool
    latency_ms: float
    error_type: str | None = None


async def check_database(database: Database, timeout_seconds: float) -> DatabaseHealth:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds), database.session() as session:
            result = await session.scalar(text("SELECT 1"))
        if result != 1:
            raise RuntimeError("Unexpected health check result")
    except Exception as exc:  # any driver, pool or network failure means unavailable
        logger.warning("Database health check failed", extra={"error_type": type(exc).__name__})
        return DatabaseHealth(reachable=False, latency_ms=_elapsed_ms(started), error_type=type(exc).__name__)
    return DatabaseHealth(reachable=True, latency_ms=_elapsed_ms(started))


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
