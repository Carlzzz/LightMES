"""Create or reset the bootstrap admin account."""
import argparse

from lightmes.database import SessionLocal
from lightmes.modules.auth.service import AuthService


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or reset the bootstrap admin account")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        service = AuthService(db)
        service.initialize_default_roles()
        existing = service.user_repo.get_by_username(args.username)
        if existing is not None:
            from lightmes.shared.security import hash_password

            existing.password_hash = hash_password(args.password)
            existing.is_active = True
        else:
            service.ensure_admin_user(args.password)
        db.commit()
        print(f"Admin account ready: {args.username}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
