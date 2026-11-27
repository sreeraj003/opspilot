"""ORM models. Importing this package registers every model on ``Base.metadata``.

Alembic's ``env.py`` imports it, so a new model must be imported here to be
seen by ``alembic revision --autogenerate``.
"""

from app.models.ai_usage import AIUsageRecord
from app.models.application_setting import ApplicationSetting
from app.models.auth_event import AuthEvent
from app.models.business_term import BusinessTerm, QuestionUnderstandingEvent
from app.models.connector import ConnectorAuditEvent, ExternalAPIConnector
from app.models.password_reset_token import PasswordResetToken
from app.models.rbac import (
    AuthorizationState,
    PermissionDefinition,
    RbacAuditEvent,
    Role,
    RolePermission,
    UserPermission,
    UserRole,
)
from app.models.sql_generation import BusinessIntentRecord, SQLGenerationEvent, SQLGenerationRecord
from app.models.schema_registry import (
    SchemaChangeEvent,
    SchemaColumn,
    SchemaRelationship,
    SchemaSource,
    SchemaTable,
)
from app.models.user import User
from app.models.user_session import RetiredRefreshToken, UserSession

__all__ = [
    "AIUsageRecord",
    "ApplicationSetting",
    "AuthEvent",
    "AuthorizationState",
    "BusinessIntentRecord",
    "BusinessTerm",
    "ConnectorAuditEvent",
    "ExternalAPIConnector",
    "PasswordResetToken",
    "PermissionDefinition",
    "QuestionUnderstandingEvent",
    "RbacAuditEvent",
    "RetiredRefreshToken",
    "Role",
    "RolePermission",
    "SchemaChangeEvent",
    "SchemaColumn",
    "SchemaRelationship",
    "SchemaSource",
    "SchemaTable",
    "SQLGenerationEvent",
    "SQLGenerationRecord",
    "User",
    "UserPermission",
    "UserRole",
    "UserSession",
]
