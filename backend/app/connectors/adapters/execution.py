"""The execution adapter: trusted request in, normalised result out.

``ConnectorExecutionAdapter.execute`` accepts only a ``ValidatedQueryRequest``
(see ``app.connectors.trusted``). Before anything is sent it checks, on the
connector side:

* the request is sealed by this process's approval authority, from a
  permitted issuer, and not expired;
* it is for this connector and this connector's schema source, and was
  validated against the schema version that is current now;
* the connector is active (the fixed connector test may run on an inactive
  connector, so it can be tested before activation);
* the SQL is non-empty, contains no NUL characters and is within the
  connector's ``max_sql_length``. SQL is never truncated, and the request is
  never switched to POST when it is long: it is refused.

It then sends ``GET <endpoint>?<sql_parameter_name>=<SQL>`` (plus any configured
non-sensitive parameters) with the connector's headers, and validates and
normalises the response with the connector's ``ResponseContract``.
"""

from app.connectors.adapters.http_client import ConnectorHttpClient, RawResponse
from app.connectors.adapters.response import NormalizedResult, check_health_document, normalize, parse_json
from app.connectors.errors import ConnectorExecutionError, ErrorCategory, TrustedRequestRejectedError
from app.connectors.runtime import ConnectorRuntimeConfig
from app.connectors.trusted import CONNECTOR_TEST_ISSUER, ExecutionApprovalAuthority, ValidatedQueryRequest


class ConnectorExecutionAdapter:
    def __init__(self, http: ConnectorHttpClient, authority: ExecutionApprovalAuthority) -> None:
        self._http = http
        self._authority = authority

    def check_request(self, request: object, config: ConnectorRuntimeConfig) -> ValidatedQueryRequest:
        trusted = self._authority.verify(request)
        if trusted.connector_id != config.connector_id:
            raise TrustedRequestRejectedError("The query request is for a different connector.")
        if trusted.schema_source_id != config.schema_source_id:
            raise TrustedRequestRejectedError("The query request is for a different schema than this connector serves.")
        if trusted.schema_version != config.current_schema_version:
            raise TrustedRequestRejectedError("The schema changed after the query was validated; validate it again.")
        if not config.is_active and trusted.issuer != CONNECTOR_TEST_ISSUER:
            raise TrustedRequestRejectedError("The connector is inactive.")
        sql = trusted.exact_sql
        if not sql.strip() or "\x00" in sql:
            raise TrustedRequestRejectedError("The SQL is empty or contains invalid characters.")
        if len(sql) > config.max_sql_length:
            raise ConnectorExecutionError(
                ErrorCategory.REQUEST_TOO_LARGE,
                f"The SQL is {len(sql)} characters, more than this connector's limit of {config.max_sql_length}. "
                "It was not sent (SQL is never truncated).",
            )
        return trusted

    async def execute(self, request: ValidatedQueryRequest, config: ConnectorRuntimeConfig) -> NormalizedResult:
        trusted = self.check_request(request, config)
        params = [*config.extra_query_params, (config.sql_parameter_name, trusted.exact_sql)]
        raw = await self._http.get(
            config.url,
            params=params,
            headers=config.headers,
            timeouts=config.timeouts,
            max_bytes=config.max_response_size_bytes,
        )
        return self._normalize(raw, config)

    async def check_health(self, config: ConnectorRuntimeConfig) -> RawResponse:
        """GET the connector's own health endpoint (no SQL is sent)."""
        if config.health_check_url is None:
            raise ConnectorExecutionError(ErrorCategory.CONFIGURATION_INVALID, "The connector has no health endpoint.")
        raw = await self._http.get(
            config.health_check_url,
            params=list(config.extra_query_params),
            headers=config.headers,
            timeouts=config.timeouts,
            max_bytes=config.max_response_size_bytes,
        )
        check_health_document(parse_json(raw.body), config.contract)
        return raw

    @staticmethod
    def _normalize(raw: RawResponse, config: ConnectorRuntimeConfig) -> NormalizedResult:
        document = parse_json(raw.body)
        return normalize(document, config.contract, http_status=raw.status_code, size=raw.size)
