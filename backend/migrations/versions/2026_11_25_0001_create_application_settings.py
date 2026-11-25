"""create application_settings

Revision ID: 0001
Revises:
Create Date: 2026-11-25 07:26:07

First application table (Module 02): non-secret installation settings.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_settings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_.-]*$'", name=op.f("ck_application_settings_key_format")),
        sa.CheckConstraint(
            "description IS NULL OR char_length(description) <= 1000",
            name=op.f("ck_application_settings_description_length"),
        ),
        sa.CheckConstraint("octet_length(value::text) <= 32768", name=op.f("ck_application_settings_value_size")),
        sa.CheckConstraint("jsonb_typeof(value) <> 'null'", name=op.f("ck_application_settings_value_not_null")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_settings")),
        # Also the index for lookups and ordering by key.
        sa.UniqueConstraint("key", name=op.f("uq_application_settings_key")),
        comment="Non-secret installation settings. Never store credentials here.",
    )


def downgrade() -> None:
    op.drop_table("application_settings")
