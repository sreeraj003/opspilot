"""Dependency providers for the AI module (``LLMServiceDep`` and friends)."""

from app.ai.dependencies.deps import (
    LLMServiceDep,
    ProviderAdminServiceDep,
    ProviderConfigServiceDep,
    UsageServiceDep,
    get_ai_http_client,
    get_llm_service,
    get_provider_admin_service,
    get_provider_config_service,
    get_usage_service,
)

__all__ = [
    "LLMServiceDep",
    "ProviderAdminServiceDep",
    "ProviderConfigServiceDep",
    "UsageServiceDep",
    "get_ai_http_client",
    "get_llm_service",
    "get_provider_admin_service",
    "get_provider_config_service",
    "get_usage_service",
]
