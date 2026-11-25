"""Non-secret, installation-wide application settings (key -> JSON value).

Secrets and credentials never belong here: the service rejects secret-like keys
and values, and the API redacts them if one is ever found.
"""

from typing import Any

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import TimestampMixin, UUIDPrimaryKeyMixin

SETTING_KEY_MAX_LENGTH = 128
SETTING_DESCRIPTION_MAX_LENGTH = 1000
# Upper bound on the stored JSONB text. The API enforces a smaller limit on
# compact JSON (see app.schemas.application_setting); this is the backstop.
SETTING_VALUE_MAX_STORED_BYTES = 32768


class ApplicationSetting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "application_settings"
    __table_args__ = (
        CheckConstraint(r"key ~ '^[a-z][a-z0-9_.-]*$'", name="key_format"),
        CheckConstraint(
            f"description IS NULL OR char_length(description) <= {SETTING_DESCRIPTION_MAX_LENGTH}",
            name="description_length",
        ),
        CheckConstraint(f"octet_length(value::text) <= {SETTING_VALUE_MAX_STORED_BYTES}", name="value_size"),
        # A JSON null is not a value: delete the setting instead.
        CheckConstraint("jsonb_typeof(value) <> 'null'", name="value_not_null"),
        {"comment": "Non-secret installation settings. Never store credentials here."},
    )

    # The unique constraint's index also serves lookups by key and ordering by key,
    # so no separate index is needed.
    key: Mapped[str] = mapped_column(String(SETTING_KEY_MAX_LENGTH), unique=True)
    # Any JSON value except null. Assign a new object to change it: in-place
    # mutation of a dict or list is not tracked.
    # none_as_null: Python None becomes SQL NULL (rejected), never a JSON null.
    value: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # never include the value: it is user data
        return f"ApplicationSetting(key={self.key!r})"
