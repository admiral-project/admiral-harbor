# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import sys

from app import create_app


def print_usage():
    print("Usage: harborctl <command> [options]")
    print("")
    print("Commands:")
    print("  user <action>   Manage users")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print_usage()
        sys.exit(1 if len(sys.argv) < 2 else 0)

    command = sys.argv[1]

    if command == "user":
        from app.cli.user import handle_user

        app = create_app()
        with app.app_context():
            handle_user()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
