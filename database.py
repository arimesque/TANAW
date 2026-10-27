"""SQLite access and schema management for TANAW.

Existing databases are never reset. ``initialize_database()`` creates missing
tables and applies small, repeatable migrations recorded in
``schema_migrations``, so it is safe to run on every start.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# The database lives in the project folder unless TANAW_DATABASE overrides it.
DATABASE_PATH = Path(os.environ.get("TANAW_DATABASE") or BASE_DIR / "tanaw.db")

# Status values an administrator may assign to a public report.
REPORT_STATUSES = ("pending", "verified", "resolved", "rejected")


@contextmanager
def get_connection():
    """Open a connection that commits on success, rolls back on error, and
    always closes (sqlite3's own ``with`` block never closes the connection)."""
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _column_names(connection, table):
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


# Each migration is (id, function). Add new ones to the end; never edit old ones.
def _m001_report_review_columns(connection):
    """Track who reviewed a report and when."""
    columns = _column_names(connection, "incident_reports")
    if "reviewed_at" not in columns:
        connection.execute("ALTER TABLE incident_reports ADD COLUMN reviewed_at TEXT")
    if "reviewed_by" not in columns:
        connection.execute("ALTER TABLE incident_reports ADD COLUMN reviewed_by TEXT")


def _m002_indexes(connection):
    """Speed up dashboard filtering and recent-report queries."""
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_reports_status ON incident_reports(status)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_reports_submitted ON incident_reports(submitted_at)"
    )


def _m003_normalise_blank_text(connection):
    """Older code could store empty strings in optional marking fields."""
    connection.execute("UPDATE map_markings SET description = NULL WHERE description = ''")
    connection.execute("UPDATE map_markings SET location = NULL WHERE location = ''")


def _m004_marking_types_and_status(connection):
    """Specific marking types, plus an active/resolved status and optional
    expiry date (YYYY-MM-DD) for temporary hazards. Existing rows stay active
    and untyped."""
    columns = _column_names(connection, "map_markings")
    if "marking_type" not in columns:
        connection.execute("ALTER TABLE map_markings ADD COLUMN marking_type TEXT")
    if "status" not in columns:
        connection.execute("ALTER TABLE map_markings ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if "expires_at" not in columns:
        connection.execute("ALTER TABLE map_markings ADD COLUMN expires_at TEXT")


MIGRATIONS = [
    ("001_report_review_columns", _m001_report_review_columns),
    ("002_indexes", _m002_indexes),
    ("003_normalise_blank_text", _m003_normalise_blank_text),
    ("004_marking_types_and_status", _m004_marking_types_and_status),
]


def initialize_database():
    """Create missing tables, then apply any migrations not yet recorded."""
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS incident_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_type TEXT NOT NULL,
                description TEXT NOT NULL,
                incident_location TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                incident_time TEXT,
                time_unknown INTEGER NOT NULL DEFAULT 0,
                photo_path TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                submitted_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS barangay_vulnerability (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barangay_name TEXT NOT NULL UNIQUE,
                total_population INTEGER NOT NULL DEFAULT 0,
                senior_count INTEGER NOT NULL DEFAULT 0,
                minor_count INTEGER NOT NULL DEFAULT 0,
                dormitory_student_count INTEGER NOT NULL DEFAULT 0,
                informal_settlement_family_count INTEGER NOT NULL DEFAULT 0,
                vulnerability_level TEXT NOT NULL DEFAULT 'unassessed',
                is_sample INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS map_markings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                location TEXT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                ),
                updated_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_map_markings_category "
            "ON map_markings(category)"
        )

        connection.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                )
            )
        """)

        applied = {
            row["id"] for row in connection.execute("SELECT id FROM schema_migrations")
        }
        for migration_id, migrate in MIGRATIONS:
            if migration_id not in applied:
                migrate(connection)
                connection.execute(
                    "INSERT INTO schema_migrations (id) VALUES (?)", (migration_id,)
                )


if __name__ == "__main__":
    initialize_database()
    print(f"TANAW database initialized at: {DATABASE_PATH}")
