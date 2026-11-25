"""ORM models. Importing this package registers every model on ``Base.metadata``.

Alembic's ``env.py`` imports it, so a new model must be imported here to be
seen by ``alembic revision --autogenerate``.
"""

from app.models.application_setting import ApplicationSetting
from app.models.auth_event import AuthEvent
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.models.user_session import RetiredRefreshToken, UserSession

__all__ = ["ApplicationSetting", "AuthEvent", "PasswordResetToken", "RetiredRefreshToken", "User", "UserSession"]
