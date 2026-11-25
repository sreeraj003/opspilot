"""create rbac tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-25 10:50:20

Module 04: roles, permission definitions, role/user/direct assignments (with
history), the RBAC audit log and the authorization revision counter.

Seeds the built-in system permissions (the ones routes check, mirrored from
``app.core.authorization.SYSTEM_PERMISSIONS`` as literals so this migration
never changes when application code does), the protected ``administrator``
system role holding all of them, and revision 1. No business roles or
permissions are created: administrators define those.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('authorization_state',
    sa.Column('id', sa.SmallInteger(), nullable=False),
    sa.Column('revision', sa.BigInteger(), server_default=sa.text('1'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('id = 1', name=op.f('ck_authorization_state_single_row')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_authorization_state')),
    comment='Global authorization revision used to invalidate permission caches.'
    )
    op.create_table('permissions',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('resource_type', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('policy_definition', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('is_system', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("action ~ '^[a-z][a-z0-9_]*([.:-][a-z0-9_]+)*$'", name=op.f('ck_permissions_action_format')),
    sa.CheckConstraint("jsonb_typeof(policy_definition) = 'object'", name=op.f('ck_permissions_policy_is_object')),
    sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*([.:-][a-z0-9_]+)*$'", name=op.f('ck_permissions_key_format')),
    sa.CheckConstraint("resource_type ~ '^[a-z][a-z0-9_]*$'", name=op.f('ck_permissions_resource_type_format')),
    sa.CheckConstraint('char_length(btrim(name)) BETWEEN 1 AND 150', name=op.f('ck_permissions_name_length')),
    sa.CheckConstraint('description IS NULL OR char_length(description) <= 2000', name=op.f('ck_permissions_description_length')),
    sa.CheckConstraint('octet_length(policy_definition::text) <= 65536', name=op.f('ck_permissions_policy_size')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_permissions')),
    comment='Permission definitions. policy_definition is validated structured data, never code or SQL.'
    )
    op.create_index('ix_permissions_key_live', 'permissions', ['key'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_permissions_resource_action', 'permissions', ['resource_type', 'action'], unique=False)
    op.create_table('roles',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_system', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*([.:-][a-z0-9_]+)*$'", name=op.f('ck_roles_key_format')),
    sa.CheckConstraint('char_length(btrim(name)) BETWEEN 1 AND 100', name=op.f('ck_roles_name_length')),
    sa.CheckConstraint('description IS NULL OR char_length(description) <= 2000', name=op.f('ck_roles_description_length')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_roles')),
    comment='Roles configured by administrators. is_system rows are seeded and protected.'
    )
    op.create_index('ix_roles_key_live', 'roles', ['key'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_roles_name_live', 'roles', [sa.literal_column('lower(name)')], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_table('rbac_audit_events',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('target_user_id', sa.Uuid(), nullable=True),
    sa.Column('role_id', sa.Uuid(), nullable=True),
    sa.Column('permission_id', sa.Uuid(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_rbac_audit_events_actor_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], name=op.f('fk_rbac_audit_events_permission_id_permissions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_rbac_audit_events_role_id_roles'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['target_user_id'], ['users.id'], name=op.f('fk_rbac_audit_events_target_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rbac_audit_events')),
    comment='Append-only history of role, permission and assignment changes.'
    )
    op.create_index('ix_rbac_audit_events_created_at', 'rbac_audit_events', ['created_at'], unique=False)
    op.create_index('ix_rbac_audit_events_permission_id', 'rbac_audit_events', ['permission_id'], unique=False)
    op.create_index('ix_rbac_audit_events_role_id', 'rbac_audit_events', ['role_id'], unique=False)
    op.create_index('ix_rbac_audit_events_target_user_id', 'rbac_audit_events', ['target_user_id'], unique=False)
    op.create_table('role_permissions',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('role_id', sa.Uuid(), nullable=False),
    sa.Column('permission_id', sa.Uuid(), nullable=False),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('assigned_by', sa.Uuid(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_role_permissions_assigned_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], name=op.f('fk_role_permissions_permission_id_permissions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], name=op.f('fk_role_permissions_revoked_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_role_permissions_role_id_roles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_role_permissions')),
    comment='Permissions granted to roles. Revoked rows are kept as history.'
    )
    op.create_index('ix_role_permissions_active_pair', 'role_permissions', ['role_id', 'permission_id'], unique=True, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_index('ix_role_permissions_permission_id', 'role_permissions', ['permission_id'], unique=False)
    op.create_table('user_permissions',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('permission_id', sa.Uuid(), nullable=False),
    sa.Column('assignment_type', sa.String(length=8), nullable=False),
    sa.Column('policy_override', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('assigned_by', sa.Uuid(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.Uuid(), nullable=True),
    sa.CheckConstraint("assignment_type IN ('grant', 'deny')", name=op.f('ck_user_permissions_assignment_type_valid')),
    sa.CheckConstraint("policy_override IS NULL OR jsonb_typeof(policy_override) = 'object'", name=op.f('ck_user_permissions_override_is_object')),
    sa.CheckConstraint('expires_at IS NULL OR expires_at > assigned_at', name=op.f('ck_user_permissions_expiry_after_assignment')),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_user_permissions_assigned_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], name=op.f('fk_user_permissions_permission_id_permissions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], name=op.f('fk_user_permissions_revoked_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_permissions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_permissions')),
    comment='Permissions granted (or explicitly denied) to one user. Revoked rows are kept as history.'
    )
    op.create_index('ix_user_permissions_active_pair', 'user_permissions', ['user_id', 'permission_id'], unique=True, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_index('ix_user_permissions_permission_id', 'user_permissions', ['permission_id'], unique=False)
    op.create_table('user_roles',
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role_id', sa.Uuid(), nullable=False),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('assigned_by', sa.Uuid(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by', sa.Uuid(), nullable=True),
    sa.CheckConstraint('expires_at IS NULL OR expires_at > assigned_at', name=op.f('ck_user_roles_expiry_after_assignment')),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_user_roles_assigned_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], name=op.f('fk_user_roles_revoked_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_user_roles_role_id_roles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_roles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_roles')),
    comment='Roles assigned to users. Revoked rows are kept as history; expired rows grant nothing.'
    )
    op.create_index('ix_user_roles_active_pair', 'user_roles', ['user_id', 'role_id'], unique=True, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_index('ix_user_roles_role_id', 'user_roles', ['role_id'], unique=False)
    _seed()


# Literal copy of the system permissions at revision 0003. Later migrations add new ones.
_SYSTEM_PERMISSIONS = [
    ('users:read', 'View users', 'List and view user accounts.'),
    ('users:manage', 'Manage users', 'Create, edit, activate, deactivate and delete user accounts.'),
    ('settings:read', 'View settings', 'Read application settings.'),
    ('settings:manage', 'Manage settings', 'Change application settings.'),
    ('roles:read', 'View roles', 'List and view roles.'),
    ('roles:manage', 'Manage roles', 'Create, edit and delete roles and change their permissions.'),
    ('permissions:read', 'View permissions', 'List and view permissions.'),
    ('permissions:manage', 'Manage permissions', 'Create, edit and delete permission definitions.'),
    ('access:review', 'Review user access', "View other users' roles and effective permissions and run authorization checks for them."),
    ('access:assign', 'Assign access', 'Assign and revoke roles and direct permissions of users.'),
]


def _seed() -> None:
    permissions = sa.table(
        "permissions",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("resource_type", sa.String),
        sa.column("action", sa.String),
        sa.column("policy_definition", postgresql.JSONB),
        sa.column("is_system", sa.Boolean),
    )
    op.bulk_insert(
        permissions,
        [
            {
                "key": key,
                "name": name,
                "description": description,
                "resource_type": "system",
                "action": key,
                "policy_definition": {"version": 1, "effect": "allow"},
                "is_system": True,
            }
            for key, name, description in _SYSTEM_PERMISSIONS
        ],
    )
    roles = sa.table(
        "roles",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("is_system", sa.Boolean),
    )
    op.bulk_insert(
        roles,
        [
            {
                "key": "administrator",
                "name": "Administrator",
                "description": "Manages users, roles, permissions and settings. Protected system role.",
                "is_system": True,
            }
        ],
    )
    op.execute(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "SELECT r.id, p.id FROM roles r CROSS JOIN permissions p "
        "WHERE r.key = 'administrator' AND r.is_system AND p.resource_type = 'system' AND p.is_system"
    )
    op.execute("INSERT INTO authorization_state (id, revision) VALUES (1, 1)")


def downgrade() -> None:
    op.drop_index('ix_user_roles_role_id', table_name='user_roles')
    op.drop_index('ix_user_roles_active_pair', table_name='user_roles', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_table('user_roles')
    op.drop_index('ix_user_permissions_permission_id', table_name='user_permissions')
    op.drop_index('ix_user_permissions_active_pair', table_name='user_permissions', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_table('user_permissions')
    op.drop_index('ix_role_permissions_permission_id', table_name='role_permissions')
    op.drop_index('ix_role_permissions_active_pair', table_name='role_permissions', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_table('role_permissions')
    op.drop_index('ix_rbac_audit_events_target_user_id', table_name='rbac_audit_events')
    op.drop_index('ix_rbac_audit_events_role_id', table_name='rbac_audit_events')
    op.drop_index('ix_rbac_audit_events_permission_id', table_name='rbac_audit_events')
    op.drop_index('ix_rbac_audit_events_created_at', table_name='rbac_audit_events')
    op.drop_table('rbac_audit_events')
    op.drop_index('ix_roles_name_live', table_name='roles', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_roles_key_live', table_name='roles', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_table('roles')
    op.drop_index('ix_permissions_resource_action', table_name='permissions')
    op.drop_index('ix_permissions_key_live', table_name='permissions', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_table('permissions')
    op.drop_table('authorization_state')
