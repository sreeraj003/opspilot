"""The asynchronous HTTPX client every connector request goes through.

One ``ConnectorHttpClient`` (one pooled ``httpx.AsyncClient``) exists per
backend process. It is created in the application lifespan and closed on
shutdown, so connections are reused instead of opened per request.

For each request it:

1. validates and resolves the destination (``DestinationPolicy.resolve``) and
   connects to the **validated address**, not to whatever the name resolves to
   a moment later: the URL is rewritten to that IP, while the ``Host`` header
   and TLS server name (SNI and certificate check) keep the configured host
   name. DNS rebinding therefore cannot move the connection;
2. builds the query string with HTTPX (RFC 3986 percent-encoding, ``%20`` for
   spaces) and refuses URLs longer than the configured limit;
3. sends a ``GET`` with redirects disabled, TLS verification on (with an
   optional extra CA bundle, never off), no proxy from the environment and no
   cookie storage, under connect, read and overall timeouts;
4. refuses redirects, non-2xx statuses and non-JSON content types, and reads
   the body as a stream, stopping as soon as it exceeds the size limit.

Errors are mapped to ``ErrorCategory`` values with safe messages. Neither the
SQL, the headers, the credentials nor the resolved address is logged.

Every error also says whether the request may already have reached the
company API (``outcome_uncertain``): failures before the request is written
(destination checks, DNS, the URL limit, connecting, TLS) are certain; a read
timeout, a connection lost after sending, or a body cut off are not, because
the company backend may have run the query. Callers must never retry those
automatically.
"""

import asyncio
import logging
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import quote, urlencode

import certifi
import httpx

from app.connectors.errors import ConnectorExecutionError, ErrorCategory
from app.connectors.security.destinations import DestinationError, DestinationPolicy

logger = logging.getLogger("app.connectors")

USER_AGENT = "OpsPilot-Connector/1.0"
_JSON_TYPES = ("application/json",)


@dataclass(frozen=True, slots=True)
class Timeouts:
    connect: float
    read: float
    total: float


@dataclass(slots=True)
class _Dispatch:
    """How far one request got: ``stage`` is ``preparing``, ``sending`` or ``reading``.

    ``traced``/``sent`` come from the HTTP transport's trace events where it reports them
    (the real network transport does; test transports may not). Without trace events the
    request is assumed possibly sent once it was handed to the transport.
    """

    stage: str = "preparing"
    traced: bool = False
    sent: bool = False

    @property
    def maybe_sent(self) -> bool:
        if self.stage == "preparing":
            return False
        return self.sent or not self.traced

    async def trace(self, name: str, info: dict[str, object]) -> None:
        self.traced = True
        if "send_request_headers" in name or "send_request_body" in name:
            self.sent = True


_CONNECT_PHASE_TIMEOUTS = (httpx.ConnectTimeout, httpx.PoolTimeout)


@dataclass(frozen=True, slots=True)
class RawResponse:
    status_code: int
    content_type: str
    body: bytes

    @property
    def size(self) -> int:
        return len(self.body)


def encode_query(params: Sequence[tuple[str, str]]) -> str:
    """``a=1&sql=SELECT%20COUNT%28%2A%29`` - every reserved character percent-encoded, spaces as %20."""
    return urlencode(list(params), quote_via=quote, safe="")


def _is_json(content_type: str) -> bool:
    media = content_type.split(";", 1)[0].strip().lower()
    return media in _JSON_TYPES or (media.startswith("application/") and media.endswith("+json"))


def _has_tls_cause(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def build_ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    if ca_bundle:
        context.load_verify_locations(cafile=ca_bundle)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # create_default_context already sets CERT_REQUIRED and check_hostname; state it explicitly.
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context


class ConnectorHttpClient:
    def __init__(
        self,
        policy: DestinationPolicy,
        *,
        max_url_length: int = 8192,
        max_connections: int = 20,
        max_keepalive: int = 10,
        ca_bundle: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.policy = policy
        self.max_url_length = max_url_length
        self._client = httpx.AsyncClient(
            verify=build_ssl_context(ca_bundle) if transport is None else True,
            transport=transport,
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_keepalive),
            follow_redirects=False,
            trust_env=False,  # never route connector traffic through environment proxies or .netrc
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),  # store no cookies
        )

    @classmethod
    def from_settings(cls, settings: object, *, transport: httpx.AsyncBaseTransport | None = None) -> "ConnectorHttpClient":
        return cls(
            DestinationPolicy.from_settings(settings),
            max_url_length=settings.connector_max_url_length,  # type: ignore[attr-defined]
            max_connections=settings.connector_pool_max_connections,  # type: ignore[attr-defined]
            max_keepalive=settings.connector_pool_max_keepalive,  # type: ignore[attr-defined]
            ca_bundle=settings.connector_ca_bundle,  # type: ignore[attr-defined]
            transport=transport,
        )

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        params: Sequence[tuple[str, str]],
        headers: dict[str, str],
        timeouts: Timeouts,
        max_bytes: int,
    ) -> RawResponse:
        dispatch = _Dispatch()
        try:
            async with asyncio.timeout(timeouts.total):
                return await self._get(
                    url, params=params, headers=headers, timeouts=timeouts, max_bytes=max_bytes, dispatch=dispatch
                )
        except TimeoutError:
            raise ConnectorExecutionError(
                ErrorCategory.TIMEOUT, "The company API did not respond in time.", outcome_uncertain=dispatch.maybe_sent
            ) from None

    async def _get(
        self,
        url: str,
        *,
        params: Sequence[tuple[str, str]],
        headers: dict[str, str],
        timeouts: Timeouts,
        max_bytes: int,
        dispatch: _Dispatch,
    ) -> RawResponse:
        try:
            destination = await self.policy.resolve(url)
        except DestinationError as exc:
            category = (
                ErrorCategory.DNS_RESOLUTION_FAILED if exc.reason == "dns_resolution_failed" else ErrorCategory.DESTINATION_REJECTED
            )
            raise ConnectorExecutionError(category, exc.message) from None

        target = destination.url
        query = encode_query(params)
        # The length limit applies to the URL as the company backend and any proxy in front of it see it.
        visible_length = len(target.origin) + len(target.path or "/") + (1 + len(query) if query else 0)
        if visible_length > self.max_url_length:
            raise ConnectorExecutionError(
                ErrorCategory.REQUEST_TOO_LARGE,
                f"The request URL would be {visible_length} characters, more than the allowed {self.max_url_length}. "
                "The SQL was not sent (it is never truncated).",
            )
        address = destination.address
        pinned = httpx.URL(
            scheme=target.scheme,
            host=str(address),
            port=target.port,
            path=target.path or "/",
            query=query.encode("ascii"),
        )
        request_headers = {
            **headers,
            "Host": target.host_header,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        extensions: dict[str, object] = {
            "timeout": httpx.Timeout(connect=timeouts.connect, read=timeouts.read, write=timeouts.read, pool=timeouts.connect).as_dict()
        }
        if target.scheme == "https" and target.ip_literal is None:
            # TLS server name and certificate verification use the configured host name, not the IP.
            extensions["sni_hostname"] = target.host
        extensions["trace"] = dispatch.trace
        request = httpx.Request("GET", pinned, headers=request_headers, extensions=extensions)

        dispatch.stage = "sending"
        try:
            response = await self._client.send(request, stream=True, follow_redirects=False)
        except _CONNECT_PHASE_TIMEOUTS:
            raise ConnectorExecutionError(ErrorCategory.TIMEOUT, "The company API did not respond in time.") from None
        except httpx.TimeoutException:
            # Read or write timeout: the request may have been received and executed.
            raise ConnectorExecutionError(
                ErrorCategory.TIMEOUT, "The company API did not respond in time.", outcome_uncertain=dispatch.maybe_sent
            ) from None
        except httpx.ConnectError as exc:
            if _has_tls_cause(exc):
                raise ConnectorExecutionError(
                    ErrorCategory.TLS_FAILED, "The company API's TLS certificate could not be verified."
                ) from None
            raise ConnectorExecutionError(ErrorCategory.CONNECTION_FAILED, "Could not connect to the company API.") from None
        except httpx.HTTPError as exc:
            if _has_tls_cause(exc):
                raise ConnectorExecutionError(ErrorCategory.TLS_FAILED, "The TLS connection to the company API failed.") from None
            raise ConnectorExecutionError(
                ErrorCategory.CONNECTION_FAILED, "The connection to the company API failed.", outcome_uncertain=dispatch.maybe_sent
            ) from None

        dispatch.stage = "reading"
        dispatch.sent = True
        try:
            return await self._read(response, max_bytes)
        finally:
            await response.aclose()

    async def _read(self, response: httpx.Response, max_bytes: int) -> RawResponse:
        status = response.status_code
        if 300 <= status < 400:
            raise ConnectorExecutionError(
                ErrorCategory.REDIRECT_REJECTED,
                "The company API answered with a redirect. Redirects are not followed: configure the final URL instead.",
                http_status=status,
            )
        if status in (401, 403):
            raise ConnectorExecutionError(
                ErrorCategory.AUTHENTICATION_FAILED, "The company API rejected the connector's credentials.", http_status=status
            )
        if not 200 <= status < 300:
            raise ConnectorExecutionError(
                ErrorCategory.HTTP_ERROR, f"The company API answered with HTTP status {status}.", http_status=status
            )
        content_type = response.headers.get("content-type", "")
        if not _is_json(content_type):
            raise ConnectorExecutionError(
                ErrorCategory.INVALID_CONTENT_TYPE, "The company API did not return JSON (Content-Type must be application/json).",
                http_status=status,
            )
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            raise _too_large(max_bytes, status)
        chunks: list[bytes] = []
        size = 0
        try:
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise _too_large(max_bytes, status)
                chunks.append(chunk)
        except httpx.TimeoutException:
            raise ConnectorExecutionError(
                ErrorCategory.TIMEOUT, "The company API did not finish its response in time.", http_status=status,
                outcome_uncertain=True,
            ) from None
        except httpx.HTTPError:
            raise ConnectorExecutionError(
                ErrorCategory.CONNECTION_FAILED, "The connection broke while reading the response.", http_status=status,
                outcome_uncertain=True,
            ) from None
        return RawResponse(status_code=status, content_type=content_type, body=b"".join(chunks))


def _too_large(max_bytes: int, status: int) -> ConnectorExecutionError:
    return ConnectorExecutionError(
        ErrorCategory.RESPONSE_TOO_LARGE, f"The company API's response is larger than the allowed {max_bytes} bytes.", http_status=status
    )

