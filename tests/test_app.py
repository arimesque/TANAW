import io
import re
import sqlite3
from datetime import datetime, timedelta

import app as tanaw_app
import database
from conftest import PNG, api, csrf, login, report_form


# ---------- Public pages and security basics ----------
def test_public_pages_load(client):
    for path in ("/", "/map", "/report", "/admin/login"):
        assert client.get(path).status_code == 200, path


def test_static_assets_served(client):
    for path in ("/static/css/style.css", "/static/js/map.js", "/static/js/icons.js",
                 "/static/vendor/leaflet/leaflet.js", "/static/data/bacolor_barangays.geojson",
                 "/static/icons/sprite.svg"):
        assert client.get(path).status_code == 200, path


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"


def test_unknown_page_is_friendly_404(client):
    r = client.get("/nope")
    assert r.status_code == 404 and b"Page not found" in r.data


def test_no_jinja_or_inline_script_in_static_js(client):
    from pathlib import Path
    for js in Path(tanaw_app.app.static_folder, "js").glob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert "{{" not in text and "{%" not in text, js.name


# ---------- Authentication ----------
def test_admin_pages_require_login(client):
    for path in ("/dashboard", "/dashboard/report/1", "/dashboard/vulnerability"):
        r = client.get(path)
        assert r.status_code == 302 and "/admin/login" in r.location, path


def test_login_logout_and_next_redirect(client):
    r = login(client, next_page="/dashboard/vulnerability")
    assert r.status_code == 302 and r.location.endswith("/dashboard/vulnerability")
    assert client.get("/dashboard").status_code == 200
    token = csrf(client)
    assert client.post("/admin/logout", data={"csrf_token": token}).status_code == 302
    assert client.get("/dashboard").status_code == 302


def test_bad_login_and_open_redirect_blocked(client):
    assert login(client, password="wrong").status_code == 401
    r = login(client, next_page="//evil.example")
    assert r.status_code == 302 and "evil" not in r.location


def test_login_throttle(client):
    for _ in range(5):
        assert login(client, password="wrong").status_code == 401
    assert login(client, password="correct-horse-battery").status_code == 429


def test_post_without_csrf_rejected(client):
    client.get("/admin/login")  # establish a session, but send no token
    r = client.post("/admin/login", data={"username": "admin", "password": "correct-horse-battery"})
    assert r.status_code == 302
    assert client.get("/dashboard").status_code == 302  # login did not happen


# ---------- Map marking API ----------
GOOD = {"name": "Bacolor Evacuation Center", "category": "evacuation_center",
        "location": "Poblacion", "description": "Gym", "latitude": 14.999, "longitude": 120.653}


def test_public_cannot_write_markings(client):
    assert api(client, "POST", "/api/map-markings", GOOD).status_code == 401
    assert api(client, "PUT", "/api/map-markings/1", GOOD).status_code == 401
    assert api(client, "DELETE", "/api/map-markings/1").status_code == 401
    r = api(client, "POST", "/api/map-markings", GOOD)
    assert r.is_json and "error" in r.get_json()  # JSON, never an HTML redirect


def test_missing_csrf_on_api_is_json_400(admin):
    r = admin.post("/api/map-markings", json=GOOD)
    assert r.status_code == 400 and r.is_json


def test_marking_crud_flow(admin):
    r = api(admin, "POST", "/api/map-markings", GOOD)
    assert r.status_code == 201
    marking = r.get_json()["marking"]

    listed = admin.get("/api/map-markings").get_json()["markings"]
    assert [m["id"] for m in listed] == [marking["id"]]

    # Editing without coordinates keeps the original location.
    r = api(admin, "PUT", f"/api/map-markings/{marking['id']}", {"name": "Renamed", "category": "facility"})
    assert r.status_code == 200
    updated = r.get_json()["marking"]
    assert updated["name"] == "Renamed" and updated["category"] == "facility"
    assert (updated["latitude"], updated["longitude"]) == (14.999, 120.653)

    # Deliberately moving it works.
    r = api(admin, "PUT", f"/api/map-markings/{marking['id']}",
            {"name": "Renamed", "category": "facility", "latitude": 15.01, "longitude": 120.66})
    assert r.get_json()["marking"]["latitude"] == 15.01

    assert api(admin, "DELETE", f"/api/map-markings/{marking['id']}").status_code == 200
    assert admin.get("/api/map-markings").get_json()["markings"] == []
    assert api(admin, "DELETE", f"/api/map-markings/{marking['id']}").status_code == 404

    with database.get_connection() as c:
        actions = [r["action"] for r in c.execute("SELECT action FROM audit_logs ORDER BY id")]
    assert {"map_marking_created", "map_marking_updated", "map_marking_deleted"} <= set(actions)


def test_marking_validation(admin):
    bad = [
        {**GOOD, "category": "bogus"}, {**GOOD, "name": "  "}, {**GOOD, "name": "x" * 121},
        {**GOOD, "latitude": None}, {**GOOD, "latitude": "abc"}, {**GOOD, "latitude": 95},
        {**GOOD, "longitude": 10},
        {**GOOD, "description": "d" * 1501},
    ]
    for body in bad:
        assert api(admin, "POST", "/api/map-markings", body).status_code == 400, body
    assert api(admin, "POST", "/api/map-markings", ["not", "an", "object"]).status_code == 400


def test_marking_text_is_returned_as_data_not_html(admin):
    api(admin, "POST", "/api/map-markings", {**GOOD, "name": "<script>alert(1)</script>"})
    data = admin.get("/api/map-markings").get_json()["markings"]
    assert data[0]["name"] == "<script>alert(1)</script>"  # stored raw; JS renders with textContent


def test_vulnerability_api_anonymous_blocked(client):
    r = client.get("/api/vulnerability")
    assert r.status_code == 401 and r.is_json


# ---------- Incident reports ----------
def test_submit_report_saves_pending(client):
    r = client.post("/report", data=report_form(client))
    assert r.status_code == 302 and r.location.endswith("/report/submitted")
    assert b"pending review" in client.get("/report/submitted").data
    with database.get_connection() as c:
        row = c.execute("SELECT * FROM incident_reports").fetchone()
    assert row["status"] == "pending" and row["incident_type"] == "flooding"
    assert (row["latitude"], row["longitude"]) == (15.004715, 120.648728)


def test_double_submit_saves_once(client):
    form = report_form(client)
    assert client.post("/report", data=form).status_code == 302
    assert client.post("/report", data=form).status_code == 302
    with database.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM incident_reports").fetchone()[0] == 1


def test_invalid_report_preserves_input_and_shows_errors(client):
    r = client.post("/report", data=report_form(client, incident_type="", description="Keep me"))
    html = r.get_data(as_text=True)
    assert r.status_code == 400
    assert "Please choose the type of incident." in html and "Keep me" in html
    with database.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM incident_reports").fetchone()[0] == 0


def test_report_time_rules(client):
    future = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    assert client.post("/report", data=report_form(client, incident_time=future)).status_code == 400
    assert client.post("/report", data=report_form(client, incident_time="garbage")).status_code == 400
    assert client.post("/report", data=report_form(client, incident_time="")).status_code == 400
    ok = client.post("/report", data=report_form(client, incident_time="", time_unknown="yes"))
    assert ok.status_code == 302
    with database.get_connection() as c:
        row = c.execute("SELECT incident_time, time_unknown FROM incident_reports").fetchone()
    assert row["incident_time"] is None and row["time_unknown"] == 1


def test_report_coordinate_validation(client):
    assert client.post("/report", data=report_form(client, latitude="95", longitude="120")).status_code == 400
    assert client.post("/report", data=report_form(client, latitude="15", longitude="")).status_code == 400
    assert client.post("/report", data=report_form(client, latitude="", longitude="")).status_code == 302


def test_report_without_csrf_rejected(client):
    form = report_form(client)
    form.pop("csrf_token")
    client.post("/report", data=form)
    with database.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM incident_reports").fetchone()[0] == 0


def test_photo_upload_validated_and_private(client):
    form = report_form(client)
    form["photo"] = (io.BytesIO(PNG), "evidence.png")
    assert client.post("/report", data=form, content_type="multipart/form-data").status_code == 302
    with database.get_connection() as c:
        name = c.execute("SELECT photo_path FROM incident_reports").fetchone()["photo_path"]
    assert name.endswith(".png") and (tanaw_app.UPLOAD_FOLDER / name).exists()

    # Not public: anonymous visitors are sent to login.
    assert client.get(f"/admin/incident-photo/{name}").status_code == 302
    login(client)
    assert client.get(f"/admin/incident-photo/{name}").status_code == 200
    assert client.get("/admin/incident-photo/..%2Fapp.py").status_code == 404
    assert client.get("/admin/incident-photo/notahash.png").status_code == 404


def test_fake_and_oversized_photos_rejected(client):
    fake = report_form(client)
    fake["photo"] = (io.BytesIO(b"<?php evil ?>"), "shell.jpg")
    assert client.post("/report", data=fake, content_type="multipart/form-data").status_code == 400
    wrong_ext = report_form(client)
    wrong_ext["photo"] = (io.BytesIO(PNG), "doc.pdf")
    assert client.post("/report", data=wrong_ext, content_type="multipart/form-data").status_code == 400
    big = report_form(client)
    big["photo"] = (io.BytesIO(PNG + b"0" * (11 * 1024 * 1024)), "big.png")
    assert client.post("/report", data=big, content_type="multipart/form-data").status_code == 413
    with database.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM incident_reports").fetchone()[0] == 0


def test_report_rate_limit(client):
    for _ in range(tanaw_app.REPORT_MAX_PER_HOUR):
        assert client.post("/report", data=report_form(client)).status_code == 302
    assert client.post("/report", data=report_form(client)).status_code == 429


# ---------- Dashboard ----------
def _seed_reports():
    with database.get_connection() as c:
        for t, s in [("fire", "pending"), ("fire", "verified"), ("flooding", "pending"), ("other", "resolved")]:
            c.execute("INSERT INTO incident_reports (incident_type, description, incident_location, status, latitude, longitude)"
                      " VALUES (?, 'd', 'Somewhere', ?, 15.0, 120.65)", (t, s))
        c.execute("INSERT INTO map_markings (name, category, latitude, longitude) VALUES ('A','hazard',15,120.6)")
        c.execute("INSERT INTO map_markings (name, category, latitude, longitude) VALUES ('B','hazard',15,120.6)")


def test_dashboard_figures_match_database(admin):
    _seed_reports()
    html = admin.get("/dashboard").get_data(as_text=True)
    stats = re.findall(r'<span class="stat-num">(\d+)</span><span class="stat-label">([^<]+)</span>', html)
    assert dict((label, int(n)) for n, label in stats) == {
        "Total reports": 4, "Pending review": 2, "Verified": 1, "Resolved": 1, "Map markings": 2}


def test_dashboard_filters_and_empty_state(admin):
    assert b"No incident reports yet" in admin.get("/dashboard").data
    _seed_reports()
    html = admin.get("/dashboard?status=pending&type=fire").get_data(as_text=True)
    assert html.count("<tr>") == 2  # header row + one matching report
    assert admin.get("/dashboard?status=bogus&page=abc").status_code == 200


def test_review_status_workflow(admin):
    _seed_reports()
    with database.get_connection() as c:
        rid = c.execute("SELECT id FROM incident_reports WHERE status='pending' LIMIT 1").fetchone()["id"]
    token = csrf(admin, f"/dashboard/report/{rid}")
    r = admin.post(f"/dashboard/report/{rid}/status", data={"csrf_token": token, "status": "verified"})
    assert r.status_code == 302
    with database.get_connection() as c:
        row = c.execute("SELECT status, reviewed_by FROM incident_reports WHERE id=?", (rid,)).fetchone()
        assert (row["status"], row["reviewed_by"]) == ("verified", "admin")
        assert c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='report_status_changed'").fetchone()[0] == 1
    admin.post(f"/dashboard/report/{rid}/status", data={"csrf_token": token, "status": "hacked"})
    with database.get_connection() as c:
        assert c.execute("SELECT status FROM incident_reports WHERE id=?", (rid,)).fetchone()["status"] == "verified"
    assert admin.get("/dashboard/report/99999").status_code == 404


def test_report_details_escapes_user_text(admin):
    with database.get_connection() as c:
        c.execute("INSERT INTO incident_reports (incident_type, description, incident_location) VALUES ('fire', ?, 'x')",
                  ("<img src=x onerror=alert(1)>",))
    with database.get_connection() as c:
        rid = c.execute("SELECT id FROM incident_reports").fetchone()["id"]
    html = admin.get(f"/dashboard/report/{rid}").get_data(as_text=True)
    assert "<img src=x" not in html and "&lt;img src=x" in html


def test_vulnerability_page_and_api_for_admin(admin):
    with database.get_connection() as c:
        c.execute("INSERT INTO barangay_vulnerability (barangay_name, total_population, is_sample) VALUES ('Sample Barangay A', 100, 1)")
    assert b"Sample" in admin.get("/dashboard/vulnerability").data
    assert admin.get("/api/vulnerability").get_json()["barangays"][0]["barangay_name"] == "Sample Barangay A"


def test_marking_types_status_and_expiry(admin):
    road = {**GOOD, "name": "Flooded underpass", "category": "hazard", "marking_type": "flooded_road"}
    r = api(admin, "POST", "/api/map-markings", road)
    assert r.status_code == 201
    created = r.get_json()["marking"]
    assert created["marking_type"] == "flooded_road" and created["status"] == "active"
    assert created["expires_at"] is None and created["is_active"] is True

    # Resolving, and an expiry date in the past, both make it inactive.
    r = api(admin, "PUT", f"/api/map-markings/{created['id']}", {**road, "status": "resolved"})
    assert r.get_json()["marking"]["is_active"] is False
    r = api(admin, "PUT", f"/api/map-markings/{created['id']}", {**road, "status": "active", "expires_at": "2020-01-01"})
    assert r.get_json()["marking"]["status"] == "active" and r.get_json()["marking"]["is_active"] is False
    r = api(admin, "PUT", f"/api/map-markings/{created['id']}", {**road, "status": "active", "expires_at": "2999-12-31"})
    assert r.get_json()["marking"]["is_active"] is True

    # Editing without the new fields keeps them.
    r = api(admin, "PUT", f"/api/map-markings/{created['id']}",
            {"name": "Renamed", "category": "hazard", "marking_type": "flooded_road"})
    assert r.get_json()["marking"]["expires_at"] == "2999-12-31"

    # Permanent types ignore status/expiry; untyped markings stay valid.
    hall = {**GOOD, "category": "facility", "marking_type": "barangay_hall", "status": "resolved", "expires_at": "2020-01-01"}
    saved = api(admin, "POST", "/api/map-markings", hall).get_json()["marking"]
    assert saved["status"] == "active" and saved["expires_at"] is None and saved["is_active"] is True
    assert api(admin, "POST", "/api/map-markings", GOOD).get_json()["marking"]["marking_type"] is None


def test_marking_type_validation(admin):
    bad = [
        {**GOOD, "marking_type": "flooded_road"},  # hazard type under evacuation_center
        {**GOOD, "category": "hazard", "marking_type": "nonsense"},
        {**GOOD, "category": "hazard", "marking_type": "closed_road", "status": "archived"},
        {**GOOD, "category": "hazard", "marking_type": "closed_road", "expires_at": "31/12/2026"},
        {**GOOD, "category": "hazard", "marking_type": "closed_road", "expires_at": "2026-02-30"},
    ]
    for body in bad:
        assert api(admin, "POST", "/api/map-markings", body).status_code == 400, body


# ---------- Database migration ----------
def test_migration_preserves_existing_records(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    c = sqlite3.connect(legacy)
    c.executescript("""
        CREATE TABLE incident_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, incident_type TEXT NOT NULL,
            description TEXT NOT NULL, incident_location TEXT NOT NULL, latitude REAL, longitude REAL,
            incident_time TEXT, time_unknown INTEGER NOT NULL DEFAULT 0, photo_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending', submitted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
        CREATE TABLE map_markings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL,
            description TEXT, location TEXT, latitude REAL NOT NULL, longitude REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT 'x', updated_at TEXT NOT NULL DEFAULT 'x');
        INSERT INTO incident_reports (incident_type, description, incident_location) VALUES ('fire','keep me','here');
        INSERT INTO map_markings (name, category, description, latitude, longitude) VALUES ('M','hazard','',15,120.6);
    """)
    c.commit(); c.close()

    monkeypatch.setattr(database, "DATABASE_PATH", legacy)
    database.initialize_database()
    database.initialize_database()  # repeatable

    with database.get_connection() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(incident_reports)")}
        assert {"reviewed_at", "reviewed_by"} <= cols
        assert conn.execute("SELECT description FROM incident_reports").fetchone()[0] == "keep me"
        assert conn.execute("SELECT description FROM map_markings").fetchone()[0] is None
        row = conn.execute("SELECT marking_type, status, expires_at FROM map_markings").fetchone()
        assert (row["marking_type"], row["status"], row["expires_at"]) == (None, "active", None)
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(database.MIGRATIONS)
