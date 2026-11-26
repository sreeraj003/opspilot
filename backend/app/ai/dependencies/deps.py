"""FastAPI dependency providers for the AI module.

Later modules obtain the LLM through ``LLMServiceDep`` and never construct a
provider themselves. Tests replace any of these via ``app.dependency_overrides``
or install a mock HTTP transport on ``app.state.ai_transport`` before startup.
"""

from typing import Annotated

import httpx
from fastapi import Depends, Request

from app.ai.config.provider_config import ProviderConfigService
from app.ai.services.llm_service import LLMService
from app.ai.services.provider_admin_service import ProviderAdminService
from app.ai.services.usage_service import UsageService
from app.ai.understanding.services.terminology_service import TerminologyService
from app.ai.understanding.services.timezone_service import BusinessTimezoneService
from app.ai.understanding.services.understanding_service import QuestionUnderstandingService
from app.api.deps import AuthorizationEngineDep, DbSessionDep, SchemaRegistryServiceDep, SettingsDep


def get_provider_config_service(settings: SettingsDep) -> ProviderConfigService:
    return ProviderConfigService(settings)


ProviderConfigServiceDep = Annotated[ProviderConfigService, Depends(get_provider_config_service)]


def get_ai_http_client(request: Request) -> httpx.AsyncClient | None:
    return getattr(request.app.state, "ai_http", None)


AIHttpClientDep = Annotated[httpx.AsyncClient | None, Depends(get_ai_http_client)]


def get_usage_service(session: DbSessionDep) -> UsageService:
    return UsageService(session)


UsageServiceDep = Annotated[UsageService, Depends(get_usage_service)]


def get_llm_service(configs: ProviderConfigServiceDep, http: AIHttpClientDep, usage: UsageServiceDep) -> LLMService:
    return LLMService(configs=configs, http=http, usage=usage)


LLMServiceDep = Annotated[LLMService, Depends(get_llm_service)]


def get_provider_admin_service(
    session: DbSessionDep, configs: ProviderConfigServiceDep, llm: LLMServiceDep, usage: UsageServiceDep
) -> ProviderAdminService:
    return ProviderAdminService(session, configs=configs, llm=llm, usage=usage)


ProviderAdminServiceDep = Annotated[ProviderAdminService, Depends(get_provider_admin_service)]


# --- question understanding and business terminology (Module 09) ------------------------------------------


def get_terminology_service(session: DbSessionDep, schemas: SchemaRegistryServiceDep) -> TerminologyService:
    return TerminologyService(session, schemas)


TerminologyServiceDep = Annotated[TerminologyService, Depends(get_terminology_service)]


def get_business_timezone_service(session: DbSessionDep, settings: SettingsDep) -> BusinessTimezoneService:
    return BusinessTimezoneService(session, settings)


def get_question_understanding_service(
    session: DbSessionDep,
    settings: SettingsDep,
    llm: LLMServiceDep,
    terminology: TerminologyServiceDep,
    engine: AuthorizationEngineDep,
    timezone: Annotated[BusinessTimezoneService, Depends(get_business_timezone_service)],
) -> QuestionUnderstandingService:
    return QuestionUnderstandingService(
        session, settings=settings, llm=llm, terminology=terminology, engine=engine, timezone=timezone
    )


QuestionUnderstandingServiceDep = Annotated[QuestionUnderstandingService, Depends(get_question_understanding_service)]
