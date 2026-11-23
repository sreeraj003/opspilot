"""Administrative commands.

Create the first platform administrator (Super Admin) after running migrations:

    python -m app.cli create-platform-admin --email admin@company.com --name "Platform Admin"

The password is read from OPSPILOT_ADMIN_PASSWORD or prompted for; it is never
accepted as a command-line argument, so it does not end up in shell history.
"""

import argparse
import getpass
import os
import sys

from pydantic import TypeAdapter, ValidationError

from app.core.config import Settings, get_settings
from app.database.session import Database
from app.models.organization import Organization
from app.models.user import User
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.rbac_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import NormalizedEmail
from app.security.passwords import PasswordHasher, PasswordPolicy
from app.security.rbac import PLATFORM_ADMIN_ROLE

PLATFORM_ORGANIZATION_SLUG = "platform"


def create_platform_admin(
    database: Database, *, email: str, full_name: str, password: str, settings: Settings | None = None
) -> User:
    settings = settings or get_settings()
    email = TypeAdapter(NormalizedEmail).validate_python(email)
    problems = PasswordPolicy.from_settings(settings).violations(password, email=email)
    if problems:
        raise ValueError("Password does not meet the password policy: " + " ".join(problems))
    with database.session() as session:
        users = UserRepository(session)
        organizations = OrganizationRepository(session)
        if users.email_exists(email):
            raise ValueError("An account with this email already exists.")
        roles = RoleRepository(session).get_by_codes([PLATFORM_ADMIN_ROLE])
        if not roles:
            raise RuntimeError("RBAC catalog is not seeded; run 'alembic upgrade head' first")
        organization = organizations.get_by_slug(PLATFORM_ORGANIZATION_SLUG) or organizations.add(
            Organization(name="OpsPilot Platform", slug=PLATFORM_ORGANIZATION_SLUG)
        )
        return users.add(
            User(
                organization_id=organization.id,
                organization=organization,
                email=email,
                full_name=full_name,
                password_hash=PasswordHasher.from_settings(settings).hash(password),
                roles=roles,
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-platform-admin", help="Create a Super Admin in the platform organization")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True, help="Full name")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_configured:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    password = os.environ.get("OPSPILOT_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    database = Database.from_settings(settings)
    try:
        user = create_platform_admin(database, email=args.email, full_name=args.name, password=password)
    except (ValueError, ValidationError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        database.dispose()
    print(f"Created platform administrator {user.email} ({user.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
