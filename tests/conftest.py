import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

# Configure an isolated database/upload folder BEFORE the app is imported.
_tmp = tempfile.mkdtemp(prefix="tanaw-test-")
os.environ["TANAW_SECRET_KEY"] = "test-secret-key"
os.environ["TANAW_DATABASE"] = str(Path(_tmp) / "test.db")
os.environ["TANAW_UPLOAD_DIR"] = str(Path(_tmp) / "uploads")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from werkzeug.security import generate_password_hash

import app as tanaw_app
import database

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def clean_state():
    """Empty every table and throttle between tests."""
    with database.get_connection() as c:
        for table in ("incident_reports", "map_markings", "audit_logs", "admin_users", "barangay_vulnerability"):
            c.execute(f"DELETE FROM {table}")
        c.execute("INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
                  ("admin", generate_password_hash("correct-horse-battery")))
    tanaw_app._login_failures.clear()
    tanaw_app._report_submissions.clear()
    yield


@pytest.fixture
def client():
    tanaw_app.app.config["TESTING"] = True
    return tanaw_app.app.test_client()


def csrf(client, path="/map"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def login(client, username="admin", password="correct-horse-battery", next_page=None):
    token = csrf(client, "/admin/login")
    data = {"csrf_token": token, "username": username, "password": password}
    if next_page:
        data["next"] = next_page
    return client.post("/admin/login", data=data)


@pytest.fixture
def admin(client):
    assert login(client).status_code == 302
    return client


def api(client, method, url, body=None, token=None):
    token = token or csrf(client)
    return client.open(url, method=method, json=body, headers={"X-CSRF-Token": token})


def report_form(client, **overrides):
    page = client.get("/report").get_data(as_text=True)
    form = {
        "csrf_token": re.search(r'name="csrf-token" content="([^"]+)"', page).group(1),
        "submission_token": re.search(r'name="submission_token" value="([^"]+)"', page).group(1),
        "incident_type": "flooding", "description": "Knee-deep water on the road.",
        "location": "Near the church", "incident_time": "2024-01-01T10:00",
        "latitude": "15.004715", "longitude": "120.648728",
    }
    form.update(overrides)
    return {k: v for k, v in form.items() if v is not None}
