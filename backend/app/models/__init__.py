"""ORM models. Importing this package registers every model on ``Base.metadata``.

Alembic's ``env.py`` imports it, so a new model must be imported here to be
seen by ``alembic revision --autogenerate``.
"""

from app.models.application_setting import ApplicationSetting

__all__ = ["ApplicationSetting"]
