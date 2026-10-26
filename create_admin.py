"""Create (or add) an administrator account for TANAW.

Usage:  python create_admin.py
"""

import getpass
import sqlite3

from werkzeug.security import generate_password_hash

from database import get_connection, initialize_database

MIN_PASSWORD_LENGTH = 10


def create_admin():
    initialize_database()

    print("TANAW Administrator Setup")
    print("-------------------------")

    username = input("Enter administrator username: ").strip()
    if not username or len(username) > 80:
        print("Username must be between 1 and 80 characters.")
        return

    # getpass hides the password while it is typed.
    password = getpass.getpass("Enter administrator password: ")
    confirm_password = getpass.getpass("Confirm administrator password: ")

    if password != confirm_password:
        print("Passwords do not match.")
        return
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")
        return

    # Only the salted hash is stored, never the password itself.
    password_hash = generate_password_hash(password)

    try:
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
                (username, password_hash),
            )
        print(f"Administrator account '{username}' created successfully.")
    except sqlite3.IntegrityError:
        print("That username already exists. Choose another username.")


if __name__ == "__main__":
    create_admin()
