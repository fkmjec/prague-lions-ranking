#!/usr/bin/env python3
import sys

from prague_lions_ranking.database import SessionLocal, engine, Base
from prague_lions_ranking.models import User, UserRole
from prague_lions_ranking.auth import get_password_hash


def create_admin():
    Base.metadata.create_all(bind=engine)

    print("Create Admin User")
    print("-" * 30)

    username = input("Username: ").strip()
    if not username:
        print("Error: Username cannot be empty")
        sys.exit(1)

    password = input("Password: ").strip()
    if not password:
        print("Error: Password cannot be empty")
        sys.exit(1)

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            print(f"Error: User '{username}' already exists")
            sys.exit(1)

        admin = User(
            username=username,
            hashed_password=get_password_hash(password),
            role=UserRole.ADMIN,
        )
        db.add(admin)
        db.commit()
        print(f"Admin user '{username}' created successfully!")
    finally:
        db.close()


if __name__ == "__main__":
    create_admin()
