"""Fixtures for Module 08 tests.

No test talks to a real provider. ``FakeProvider`` is an ``httpx.MockTransport``
handler that records every request and answers with canned OpenAI-compatible
responses (or raises transport errors), so the real adapter, error mapping,
retry policy and normalisation all run.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

API_KEY = "nvapi-test-secret-key-0123456789"
OTHER_KEY = "sk-other-provider-secret-987654"
MODEL = "meta/llama-3.1-8b-instruct"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1"


def completion(content: str = "OK", *, usage: dict[str, Any] | None | bool = True, model: str = MODEL,
               finish_reason: str | None = "stop", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
        **extra,
    }
    if usage is True:
        body["usage"] = {"prompt_tokens": 21, "completion_tokens": 2, "total_tokens": 23}
    elif isinstance(usage, dict):
        body["usage"] = usage
    return body


Responder = Callable[[httpx.Request], httpx.Response]


@dataclass
class FakeProvider:
    """Answers queued responses in order (the last one repeats); records requests."""

    requests: list[httpx.Request] = field(default_factory=list)
    queue: list[Responder] = field(default_factory=list)
    models: list[str] = field(default_factory=lambda: [MODEL, "other/model"])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models") and request.method == "GET" and not self.queue:
            return httpx.Response(200, json={"object": "list", "data": [{"id": m} for m in self.models]})
        if not self.queue:
            return httpx.Response(200, json=completion())
        responder = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        return responder(request)

    def reply(self, status: int = 200, body: Any = None, *, headers: dict[str, str] | None = None,
              raw: bytes | None = None, content_type: str = "application/json") -> "FakeProvider":
        def respond(_: httpx.Request) -> httpx.Response:
            content = raw if raw is not None else json.dumps(completion() if body is None else body).encode()
            return httpx.Response(status, content=content, headers={"content-type": content_type, **(headers or {})})

        self.queue.append(respond)
        return self

    def fail(self, exc: Exception) -> "FakeProvider":
        def respond(request: httpx.Request) -> httpx.Response:
            raise exc

        self.queue.append(respond)
        return self

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def last_json(self) -> Any:
        return json.loads(self.last.content)


@pytest.fixture
def provider_api() -> FakeProvider:
    return FakeProvider()
