"""Constraint and index naming conventions for the application database.

Applied through ``Base.metadata`` so every constraint SQLAlchemy emits, and
every constraint Alembic autogenerates, gets a predictable name::

    pk_application_settings              primary key
    uq_application_settings_key          unique constraint
    ix_application_settings_created_at   index
    fk_users_role_id_roles               foreign key (column, then referred table)
    ck_application_settings_key_format   check constraint (``name="key_format"``)

Predictable names matter because migrations drop and alter constraints by name.
Changing this mapping later would rename constraints in new migrations only,
so treat it as fixed.
"""

NAMING_CONVENTION: dict[str, str] = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    # Check constraints must be given a short ``name=``; the table prefix is added here.
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}
