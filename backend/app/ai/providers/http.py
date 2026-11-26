"""The one outbound HTTP client used by AI providers.

A single pooled ``httpx.AsyncClient`` per backend process, created in the
FastAPI lifespan and closed at shutdown (like the Module 07 connector client).
Providers receive it; they never create their own.

* Redirects are not followed, so the ``Authorization`` header is never sent to
  another host.
* TLS verification is always on.
* ``trust_env`` stays enabled so an installation behind a corporate proxy can
  set ``HTTPS_PROXY`` for the backend container. (Connector traffic is
  different: it is pinned to validated company addresses and ignores proxies.)
"""

import httpx

USER_AGENT = "OpsPilot-AI/1.0"


def create_ai_http_client(
    *, transport: httpx.AsyncBaseTransport | None = None, max_connections: int = 20
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max(1, max_connections // 2)),
        headers={"User-Agent": USER_AGENT},
        # Per-request timeouts are always passed explicitly; this is only a backstop.
        timeout=httpx.Timeout(60.0),
    )
