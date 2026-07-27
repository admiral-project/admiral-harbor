# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import argparse
import getpass
import os
import sys

from argon2 import PasswordHasher

from app.extensions import db
from app.models import Customer, HarborAdminUser

ph = PasswordHasher()


def resolve_password():
    password = os.environ.get("ADMIRAL_HARBOR_SET_PASSWORD")
    if password is not None:
        if not password:
            print("Error: ADMIRAL_HARBOR_SET_PASSWORD is set but empty.")
            sys.exit(1)
        return password
    return None


def cmd_create_admin(username):
    existing = db.session.query(HarborAdminUser).filter_by(username=username).one_or_none()
    if existing:
        print(f"Error: admin user '{username}' already exists.")
        sys.exit(1)

    password = resolve_password()
    if password is None:
        password = getpass.getpass(f"Password for admin '{username}': ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords do not match.")
            sys.exit(1)
        if not password:
            print("Error: password cannot be empty.")
            sys.exit(1)

    user = HarborAdminUser(
        username=username,
        display_name=username,
        password_hash=ph.hash(password),
    )
    db.session.add(user)
    db.session.commit()
    print(f"Admin user '{username}' created successfully.")


def cmd_create_customer(email, display_name, country):
    if not email or not display_name:
        print("Error: --email and --display-name are required for customer type.")
        sys.exit(1)

    existing = db.session.query(Customer).filter_by(email=email).one_or_none()
    if existing:
        print(f"Error: customer '{email}' already exists.")
        sys.exit(1)

    password = resolve_password()
    if password is None:
        password = getpass.getpass(f"Password for customer '{email}': ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords do not match.")
            sys.exit(1)
        if not password:
            print("Error: password cannot be empty.")
            sys.exit(1)

    customer = Customer(
        email=email.lower(),
        display_name=display_name,
        password_hash=ph.hash(password),
        country=country.upper() if country else None,
    )
    db.session.add(customer)
    db.session.commit()
    print(f"Customer '{email}' created successfully.")


def cmd_list(output):
    admins = db.session.query(HarborAdminUser).order_by(HarborAdminUser.created_at).all()
    customers = db.session.query(Customer).order_by(Customer.created_at).all()

    if output == "json":
        import json

        print(
            json.dumps(
                {
                    "admins": [
                        {
                            "username": u.username,
                            "display_name": u.display_name,
                            "is_active": u.is_active,
                            "created_at": str(u.created_at),
                        }
                        for u in admins
                    ],
                    "customers": [
                        {
                            "email": c.email,
                            "display_name": c.display_name,
                            "is_active": c.is_active,
                            "country": c.country,
                            "created_at": str(c.created_at),
                        }
                        for c in customers
                    ],
                },
                indent=2,
            )
        )
        return

    print("Admin Users:")
    print(f"{'Username':20} {'Display Name':25} {'Active':8} {'Created':20}")
    print(f"{'---':20} {'---':25} {'---':8} {'---':20}")
    for u in admins:
        print(f"{u.username:20} {(u.display_name or ''):25} {u.is_active!s:8} {u.created_at!s:20}")

    print()
    print("Customers:")
    print(f"{'Email':30} {'Display Name':25} {'Active':8} {'Country':8} {'Created':20}")
    print(f"{'---':30} {'---':25} {'---':8} {'---':8} {'---':20}")
    for c in customers:
        print(f"{c.email:30} {(c.display_name or ''):25} {c.is_active!s:8} {(c.country or ''):8} {c.created_at!s:20}")


def cmd_set_password(username):
    user = db.session.query(HarborAdminUser).filter_by(username=username).one_or_none()
    if not user:
        print(f"Error: admin user '{username}' not found.")
        sys.exit(1)

    password = resolve_password()
    if password is None:
        password = getpass.getpass(f"New password for '{username}': ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords do not match.")
            sys.exit(1)
        if not password:
            print("Error: password cannot be empty.")
            sys.exit(1)

    user.password_hash = ph.hash(password)
    db.session.commit()
    print(f"Password for '{username}' updated successfully.")


def cmd_toggle_active(username):
    user = db.session.query(HarborAdminUser).filter_by(username=username).one_or_none()
    if not user:
        print(f"Error: admin user '{username}' not found.")
        sys.exit(1)

    user.is_active = not user.is_active
    status = "activated" if user.is_active else "deactivated"
    db.session.commit()
    print(f"Admin user '{username}' {status}.")


def print_user_usage():
    print("Usage: harborctl user <action> [options]")
    print()
    print("Actions:")
    print("  create           Create a user")
    print("  list             List users")
    print("  set-password     Set password for admin user")
    print("  toggle-active    Toggle admin user active status")


def handle_user():
    if len(sys.argv) < 3 or sys.argv[2] in ("--help", "-h"):
        print_user_usage()
        sys.exit(1 if len(sys.argv) < 3 else 0)

    action = sys.argv[2]

    if action == "create":
        parser = argparse.ArgumentParser(prog="harborctl user create")
        parser.add_argument(
            "--type",
            default="admin",
            choices=["admin", "customer"],
            help="User type to create (default: admin)",
        )
        parser.add_argument("--display-name", help="Display name (required for customer)")
        parser.add_argument("--country", default="", help="Country code for customer")
        parser.add_argument("ident", nargs="?", help="Username (admin) or email (customer)")
        args = parser.parse_args(sys.argv[3:])

        if not args.ident:
            print("Error: username or email is required.")
            sys.exit(1)

        if args.type == "admin":
            cmd_create_admin(args.ident)
        elif args.type == "customer":
            cmd_create_customer(args.ident, args.display_name or "", args.country or "")

    elif action == "list":
        parser = argparse.ArgumentParser(prog="harborctl user list")
        parser.add_argument(
            "--output",
            default="table",
            choices=["table", "json"],
            help="Output format (default: table)",
        )
        args = parser.parse_args(sys.argv[3:])
        cmd_list(args.output)

    elif action == "set-password":
        if len(sys.argv) < 4:
            print("Usage: harborctl user set-password <username>")
            sys.exit(1)
        cmd_set_password(sys.argv[3])

    elif action == "toggle-active":
        if len(sys.argv) < 4:
            print("Usage: harborctl user toggle-active <username>")
            sys.exit(1)
        cmd_toggle_active(sys.argv[3])

    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
