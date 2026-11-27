"""The execution module's only way to reach the company API: Module 07's connector service.

There is no second HTTP client here and no connector security of its own.
``ConnectorDispatcher.dispatch`` hands a sealed ``ValidatedQueryRequest`` (issued
by Module 11's ``SQLExecutionGuard``) to ``ConnectorService.execute``, which
verifies the seal, SQL hash, connector, schema version and limits, claims the
approval exactly once, and sends the GET through the shared HTTPX client with
its destination policy, timeouts and streamed size limit. This adapter can
only *tighten* the connector's limits (response bytes and duration) and asks
for exact (``Decimal``) number parsing.
"""

from app.ai.query_execution.policies.limits import ExecutionLimits
from app.connectors.adapters.response import NormalizedResult
from app.connectors.trusted import ValidatedQueryRequest
from app.services.connector_service import ConnectorService


class ConnectorDispatcher:
    def __init__(self, connectors: ConnectorService, limits: ExecutionLimits) -> None:
        self._connectors = connectors
        self._limits = limits

    async def dispatch(self, request: ValidatedQueryRequest) -> NormalizedResult:
        return await self._connectors.execute(
            request,
            max_response_bytes=self._limits.max_response_bytes,
            max_total_seconds=float(self._limits.max_duration_seconds),
            precise_numbers=True,
        )
