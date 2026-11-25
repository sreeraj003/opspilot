"""create authentication tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-25 09:57:14

Module 03: users, refresh-token sessions (with retired-token digests for reuse
detection), password-reset tokens and the authentication event log. Only
digests of tokens and Argon2id password hashes are stored.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=True),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('is_verified', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('is_superuser', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('failed_login_attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("username IS NULL OR username ~ '^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$'", name=op.f('ck_users_username_format')),
    sa.CheckConstraint('char_length(btrim(full_name)) BETWEEN 1 AND 200', name=op.f('ck_users_full_name_length')),
    sa.CheckConstraint('char_length(email) BETWEEN 3 AND 320', name=op.f('ck_users_email_length')),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_users_email_lowercase')),
    sa.CheckConstraint('failed_login_attempts >= 0', name=op.f('ck_users_failed_login_attempts_non_negative')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    comment='Accounts of this installation. password_hash is Argon2id; never returned by the API.'
    )
    op.create_index('ix_users_created_at', 'users', ['created_at'], unique=False)
    op.create_index('ix_users_email_live', 'users', ['email'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_users_username_live', 'users', [sa.literal_column('lower(username)')], unique=True, postgresql_where=sa.text('deleted_at IS NULL AND username IS NOT NULL'))
    op.create_table('auth_events',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_auth_events_actor_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_events_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_events')),
    comment='Append-only security events. Never contains credentials.'
    )
    op.create_index('ix_auth_events_created_at', 'auth_events', ['created_at'], unique=False)
    op.create_index('ix_auth_events_event_type', 'auth_events', ['event_type'], unique=False)
    op.create_index('ix_auth_events_user_id', 'auth_events', ['user_id'], unique=False)
    op.create_table('password_reset_tokens',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('invalidated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_password_reset_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_password_reset_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_password_reset_tokens_token_hash')),
    comment='Password-reset tokens. Only digests are stored; each token works once.'
    )
    op.create_index('ix_password_reset_tokens_user_id', 'password_reset_tokens', ['user_id'], unique=False)
    op.create_table('user_sessions',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('refresh_token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('absolute_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_reason', sa.String(length=32), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rotated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('user_agent', sa.String(length=256), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_sessions')),
    sa.UniqueConstraint('refresh_token_hash', name=op.f('uq_user_sessions_refresh_token_hash')),
    comment='Refresh-token sessions. Only token digests are stored, never raw tokens.'
    )
    op.create_index('ix_user_sessions_expires_at', 'user_sessions', ['expires_at'], unique=False)
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'], unique=False)
    op.create_table('retired_refresh_tokens',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('retired_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['user_sessions.id'], name=op.f('fk_retired_refresh_tokens_session_id_user_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_retired_refresh_tokens')),
    comment='Digests of rotated refresh tokens, for reuse detection. Deleted with their session.'
    )
    op.create_index('ix_retired_refresh_tokens_session_id', 'retired_refresh_tokens', ['session_id'], unique=False)


def downgrade() -> None:
    # Dropping a table drops its indexes and constraints.
    op.drop_table("retired_refresh_tokens")
    op.drop_table("user_sessions")
    op.drop_table("password_reset_tokens")
    op.drop_table("auth_events")
    op.drop_table("users")
