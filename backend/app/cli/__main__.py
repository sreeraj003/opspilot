"""OpsPilot AI administration commands.

    python -m app.cli create-admin      # first administrator (once per installation)
    python -m app.cli reset-password    # recover access from the server's shell
    python -m app.cli sync-permissions  # restore built-in permissions / Administrator role

Credentials come from an interactive prompt (input is hidden) or, with
``--no-input``, from environment variables:

    OPSPILOT_ADMIN_EMAIL, OPSPILOT_ADMIN_FULL_NAME, OPSPILOT_ADMIN_PASSWORD
    (OPSPILOT_ADMIN_USERNAME optional)

Passwords are never printed, logged or accepted as command-line arguments
(arguments are visible in process listings and shell history).
"""

import argparse
import asyncio
import getpass
import logging
import os
import sys

from app.authorization.system_catalog import ensure_system_rbac
from app.core.config import get_settings
from app.core.emails import InvalidEmailError, normalize_email
from app.database.session import Database
from app.repositories.user_repository import UserRepository
from app.services.bootstrap_service import (
    AdminCredentials,
    BootstrapError,
    create_initial_admin,
    reset_password_from_host,
)
from app.services.password_service import PasswordPolicyError, PasswordService, password_policy_problem

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value and value.strip() else None


def _prompt_email(default: str | None) -> str:
    while True:
        value = default or input("Administrator email: ").strip()
        default = None
        try:
            return normalize_email(value)
        except InvalidEmailError as exc:
            print(f"  {exc}", file=sys.stderr)


def _prompt_password(identifiers: tuple[str | None, ...]) -> str:
    while True:
        first = getpass.getpass("Password (input hidden, at least 12 characters): ")
        problem = password_policy_problem(first, identifiers=identifiers)
        if problem:
            print(f"  {problem}", file=sys.stderr)
            continue
        if getpass.getpass("Repeat password: ") != first:
            print("  Passwords do not match.", file=sys.stderr)
            continue
        return first


def _credentials(args: argparse.Namespace) -> AdminCredentials:
    if args.no_input:
        email = args.email or _env("OPSPILOT_ADMIN_EMAIL")
        full_name = args.full_name or _env("OPSPILOT_ADMIN_FULL_NAME")
        password = _env("OPSPILOT_ADMIN_PASSWORD")
        missing = [
            name
            for name, value in (
                ("OPSPILOT_ADMIN_EMAIL", email),
                ("OPSPILOT_ADMIN_FULL_NAME", full_name),
                ("OPSPILOT_ADMIN_PASSWORD", password),
            )
            if value is None
        ]
        if missing:
            raise BootstrapError("Missing environment variables: " + ", ".join(missing))
        return AdminCredentials(
            email=email or "",
            full_name=full_name or "",
            password=password or "",
            username=args.username or _env("OPSPILOT_ADMIN_USERNAME"),
        )
    email = _prompt_email(args.email or _env("OPSPILOT_ADMIN_EMAIL"))
    full_name = args.full_name or _env("OPSPILOT_ADMIN_FULL_NAME")
    while not full_name:
        full_name = input("Full name: ").strip() or None
    username = args.username or _env("OPSPILOT_ADMIN_USERNAME")
    password = _prompt_password((email, username, full_name))
    return AdminCredentials(email=email, full_name=full_name, password=password, username=username)


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.database_url is None:
        print("DATABASE_URL is not set. Run this inside the backend container or set DATABASE_URL.", file=sys.stderr)
        return EXIT_USAGE
    database = Database.from_settings(settings)
    passwords = PasswordService()
    try:
        if args.command == "create-admin":
            # Checked before prompting, so nobody types a password for nothing.
            # create_initial_admin checks again under a lock.
            async with database.session() as session:
                if await UserRepository(session).superuser_exists():
                    raise BootstrapError(
                        "An installation administrator already exists. Bootstrap runs only once; "
                        "use 'python -m app.cli reset-password' to recover access."
                    )
            credentials = _credentials(args)
            async with database.session() as session:
                await create_initial_admin(session, passwords, credentials)
            print(f"Installation administrator created: {normalize_email(credentials.email)}")
            print("Sign in at the OpsPilot AI web app with this email and the password you chose.")
        elif args.command == "sync-permissions":
            async with database.session() as session:
                changes = await ensure_system_rbac(session)
            print(f"Built-in permissions and the Administrator role are up to date ({changes} change(s) made).")
        else:
            email = args.email or _env("OPSPILOT_ADMIN_EMAIL") or _prompt_email(None)
            password = _env("OPSPILOT_NEW_PASSWORD") if args.no_input else _prompt_password((email,))
            if password is None:
                raise BootstrapError("Missing environment variable: OPSPILOT_NEW_PASSWORD")
            async with database.session() as session:
                await reset_password_from_host(session, passwords, email, password)
            print("Password updated. Every existing session of this account has been signed out.")
        return EXIT_OK
    except (BootstrapError, PasswordPolicyError, InvalidEmailError) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. Nothing was changed.", file=sys.stderr)
        return EXIT_USAGE
    finally:
        await database.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="OpsPilot AI administration commands.")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-admin", help="Create the first installation administrator (runs once).")
    create.add_argument("--email")
    create.add_argument("--full-name")
    create.add_argument("--username")
    create.add_argument(
        "--no-input", action="store_true", help="Read credentials from OPSPILOT_ADMIN_* environment variables."
    )

    reset = commands.add_parser("reset-password", help="Set a new password for an account from the server shell.")
    reset.add_argument("--email")
    reset.add_argument("--no-input", action="store_true", help="Read the password from OPSPILOT_NEW_PASSWORD.")

    commands.add_parser(
        "sync-permissions", help="Create any missing built-in permission or the Administrator role (idempotent)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Keep library logs quiet: this command prints its own outcome and never logs credentials.
    logging.basicConfig(level=logging.WARNING)
    if sys.platform == "win32":  # psycopg async needs a selector event loop on Windows
        return asyncio.run(_run(args), loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
