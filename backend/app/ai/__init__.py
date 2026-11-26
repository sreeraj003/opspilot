"""AI provider integration and the LLM service layer (Module 08).

Application code talks to language models only through
``app.ai.services.llm_service.LLMService``. Providers (``app.ai.providers``)
are interchangeable implementations of ``LLMProvider``; adding one never
changes business logic. See docs/AI_PROVIDERS.md.

Sub-packages:

* ``config``       provider configuration built from the central ``Settings``
* ``exceptions``   typed provider errors with safe, fixed client messages
* ``schemas``      ``LLMMessage``, ``LLMGenerationRequest``, ``LLMGenerationResponse`` and admin views
* ``providers``    the provider interface, the OpenAI-compatible adapter, NVIDIA, the registry
* ``services``     ``LLMService`` (selection, validation, retries, normalisation), usage tracking, admin
* ``dependencies`` FastAPI providers for the above
"""
