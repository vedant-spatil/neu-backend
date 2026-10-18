"""Create or update an investigator account.

Usage (inside the api container or locally with the venv):
    python -m app.cli.create_user --email a@x.com --password '...'
Password may also be read from the AML_PASSWORD env var to keep it out of shell history.
"""

import argparse
import os
import sys

from sqlalchemy import select

from app.auth.passwords import hash_password
from app.db.models import User
from app.db.session import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or reset an investigator user.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", default=os.environ.get("AML_PASSWORD"))
    parser.add_argument("--role", choices=["admin", "qa"], help="required when creating a user")
    parser.add_argument("--allow-weak", action="store_true",
                        help="local demo only: skip the 12-character minimum")
    args = parser.parse_args()
    if not args.password or (len(args.password) < 12 and not args.allow_weak):
        print("password required (min 12 chars, or --allow-weak for local demo), via --password or AML_PASSWORD",
              file=sys.stderr)
        return 2

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.email == args.email)).scalar_one_or_none()
        if user is None:
            db.add(User(email=args.email, password_hash=hash_password(args.password), role=args.role or "qa"))
            action = "created"
        else:
            user.password_hash = hash_password(args.password)
            if args.role:
                user.role = args.role
            user.is_active = True
            action = "updated"
        db.commit()
    print(f"user {action}: {args.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
