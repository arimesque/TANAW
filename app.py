"""TANAW - Territorial Analysis and Network for Accident Watch.

Flask application for the MDRRMO Bacolor incident map. Public visitors can view
the map and submit incident reports; authenticated administrators review
reports and manage map markings. Every administrative operation is enforced on
the server, never only hidden in the interface.
"""

import hmac
import os
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import database
from database import REPORT_STATUSES, get_connection, initialize_database

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
app = Flask(__name__)

# The secret key signs session cookies. Refuse to start without one rather
# than falling back to an insecure default.
app.config["SECRET_KEY"] = os.environ.get("TANAW_SECRET_KEY")
if not app.config["SECRET_KEY"]:
    raise RuntimeError(
        "TANAW_SECRET_KEY is not set. Generate one with "
        '`python -c "import secrets; print(secrets.token_hex(32))"` '
        "and set it before starting the application (see README.md)."
    )

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB per request
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("TANAW_HTTPS", "").lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.json.sort_keys = False

# Private folder for incident photos. It is outside static/ on purpose.
UPLOAD_FOLDER = Path(
    os.environ.get("TANAW_UPLOAD_DIR") or Path(__file__).resolve().parent / "uploads"
)
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

# The Philippines has no daylight saving time, so a fixed UTC+8 offset is exact
# (and avoids needing the tzdata package on Windows).
PH_TZ = timezone(timedelta(hours=8))

INCIDENT_TYPES = {
    "vehicular_accident": "Vehicular accident",
    "flooding": "Flooding",
    "road_obstruction": "Road obstruction",
    "fire": "Fire",
    "other": "Other emergency",
}
STATUS_LABELS = {
    "pending": "Pending review",
    "verified": "Verified",
    "resolved": "Resolved",
    "rejected": "Rejected",
}
MARKING_CATEGORIES = {
    "incident": "Incident",
    "facility": "Facility",
    "evacuation_center": "Evacuation center",
    "hazard": "Hazard",
    "other": "Other",
}
# Specific marking types, grouped under the existing categories. Types flagged
# temporary are short-lived hazards, so they may be resolved or given an expiry.
MARKING_TYPES = {
    "flooded_road": {"category": "hazard", "label": "Flooded road", "temporary": True},
    "road_construction": {"category": "hazard", "label": "Road construction", "temporary": True},
    "closed_road": {"category": "hazard", "label": "Closed road", "temporary": True},
    "road_damage": {"category": "hazard", "label": "Potholes / road damage", "temporary": True},
    "fallen_tree_debris": {"category": "incident", "label": "Fallen tree / debris", "temporary": True},
    "landslide": {"category": "incident", "label": "Landslide", "temporary": True},
    "barangay_hall": {"category": "facility", "label": "Barangay hall", "temporary": False},
    "health_center": {"category": "facility", "label": "Health center", "temporary": False},
    "fire_station": {"category": "facility", "label": "Fire station", "temporary": False},
}
MARKING_STATUSES = ("active", "resolved")

# Loose bounding box of the Philippines; catches swapped or garbage coordinates.
PH_LAT_RANGE = (4.0, 22.0)
PH_LON_RANGE = (116.0, 127.0)

# Photo uploads are checked by content ("magic bytes"), not just file extension.
PHOTO_SIGNATURES = {
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "webp": (b"RIFF",),  # also checked for "WEBP" at byte 8 below
}
PHOTO_NAME_PATTERN = re.compile(r"^[0-9a-f]{32}\.(jpg|jpeg|png|webp)$")

initialize_database()


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def wants_json():
    """API routes always answer with JSON, including for errors."""
    return request.path.startswith("/api/")


def json_error(message, status):
    return jsonify({"error": message}), status


def current_admin_name():
    return session.get("admin_username", "unknown")


def record_audit(action, details=None):
    """Write an audit entry, always naming the acting administrator."""
    actor = current_admin_name() if "admin_id" in session else "anonymous"
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO audit_logs (action, details) VALUES (?, ?)",
            (action, f"[{actor}] {details}" if details else f"[{actor}]"),
        )


def admin_required(view):
    """Protect a route. Pages redirect to the login form; API calls get a JSON
    401 so the browser code never tries to parse an HTML login page."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "admin_id" not in session:
            if wants_json():
                return json_error("Administrator login required.", 401)
            flash("Please log in to access the administration pages.", "error")
            return redirect(url_for("admin_login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def parse_coordinates(latitude_value, longitude_value):
    """Return (lat, lon) as floats, or raise ValueError."""
    latitude = float(latitude_value)
    longitude = float(longitude_value)
    if not (PH_LAT_RANGE[0] <= latitude <= PH_LAT_RANGE[1]
            and PH_LON_RANGE[0] <= longitude <= PH_LON_RANGE[1]):
        raise ValueError("outside the Philippines")
    return latitude, longitude


def safe_next(target):
    """Allow redirects only to local paths (prevents open redirects)."""
    if (target and target.startswith("/") and not target.startswith("//")
            and "\\" not in target):
        return target
    return None


# Simple in-memory throttles. They reset when the server restarts and are per
# process, which is adequate for a single-server deployment.
_login_failures = defaultdict(deque)
_report_submissions = defaultdict(deque)
LOGIN_MAX_FAILURES, LOGIN_WINDOW = 5, 15 * 60
REPORT_MAX_PER_HOUR = 10


def _recent(events, window):
    cutoff = time.time() - window
    while events and events[0] < cutoff:
        events.popleft()
    return events


# --------------------------------------------------------------------------
# CSRF protection, security headers, template helpers
# --------------------------------------------------------------------------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@app.before_request
def verify_csrf():
    """All state-changing requests must carry the session's CSRF token."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("csrf", "")
        if not expected or not hmac.compare_digest(sent, expected):
            if wants_json():
                return json_error("Your session expired. Reload the page and try again.", 400)
            flash("Your session expired. Please try again.", "error")
            return redirect(request.referrer or url_for("home"))


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(self)")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https://tile.openstreetmap.org; "
        "connect-src 'self' https://nominatim.openstreetmap.org; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
    )
    # Administrative pages and API answers must never be cached or shared.
    if request.path.startswith(("/dashboard", "/admin", "/api/vulnerability")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.context_processor
def inject_globals():
    return {
        "csrf_token": csrf_token,
        "is_admin": "admin_id" in session,
        "admin_username": session.get("admin_username"),
        "INCIDENT_TYPES": INCIDENT_TYPES,
        "STATUS_LABELS": STATUS_LABELS,
        "MARKING_CATEGORIES": MARKING_CATEGORIES,
    }


@app.template_filter("ph_datetime")
def ph_datetime(value):
    """Format a stored UTC timestamp (…Z) in Philippine time."""
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(PH_TZ).strftime("%d %b %Y, %I:%M %p")
    except ValueError:
        return str(value)


@app.template_filter("incident_datetime")
def incident_datetime(value):
    """Format the reporter-entered local incident time (already Philippine time)."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M").strftime("%d %b %Y, %I:%M %p")
    except (TypeError, ValueError):
        return "—"


# --------------------------------------------------------------------------
# Public pages
# --------------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/map")
def map_page():
    return render_template("map.html")


# --------------------------------------------------------------------------
# Map markings API
# --------------------------------------------------------------------------
MARKING_COLUMNS = (
    "id, name, category, marking_type, status, expires_at, description, location, "
    "latitude, longitude, created_at, updated_at"
)


def marking_to_dict(row):
    """API shape for a marking; `is_active` is false once resolved or expired
    (expiry is the last day the marking applies, in Philippine time)."""
    marking = dict(row)
    today = datetime.now(PH_TZ).date().isoformat()
    marking["is_active"] = marking["status"] == "active" and (
        not marking["expires_at"] or marking["expires_at"] >= today
    )
    return marking


def parse_marking_payload(data, existing=None):
    """Validate a marking payload. Returns (clean_values, error_message).

    When editing, omitted coordinates keep the marking's original location, so
    an edit never moves a marking unless the administrator changed it.
    """
    if not isinstance(data, dict):
        return None, "The request must contain a JSON object."

    name = str(data.get("name", "")).strip()
    category = str(data.get("category", "")).strip().lower()
    description = str(data.get("description") or "").strip()
    location = str(data.get("location") or "").strip()

    if not name or len(name) > 120:
        return None, "Name is required and must be 120 characters or fewer."
    if category not in MARKING_CATEGORIES:
        return None, "Please select a valid marking category."
    if len(description) > 1500:
        return None, "Additional details must be 1,500 characters or fewer."
    if len(location) > 250:
        return None, "The location description must be 250 characters or fewer."

    # Omitted type/status/expiry keep the saved values when editing.
    def pick(key, default):
        if key in data:
            return data.get(key)
        return existing[key] if existing is not None else default

    marking_type = str(pick("marking_type", "") or "").strip().lower()
    status = str(pick("status", "active") or "active").strip().lower()
    expires_at = str(pick("expires_at", "") or "").strip()

    if marking_type:
        type_info = MARKING_TYPES.get(marking_type)
        if type_info is None or type_info["category"] != category:
            return None, "Please select a marking type that belongs to the chosen category."
    if status not in MARKING_STATUSES:
        return None, "Status must be active or resolved."
    if expires_at:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires_at):
                raise ValueError
            datetime.strptime(expires_at, "%Y-%m-%d")
        except ValueError:
            return None, "Expiry date must be a valid date (YYYY-MM-DD)."
    # Only temporary hazards can be resolved or expire; everything else stays active.
    if not (marking_type and MARKING_TYPES[marking_type]["temporary"]):
        status, expires_at = "active", ""

    if existing is not None and "latitude" not in data and "longitude" not in data:
        latitude, longitude = existing["latitude"], existing["longitude"]
    else:
        try:
            latitude, longitude = parse_coordinates(data.get("latitude"), data.get("longitude"))
        except (TypeError, ValueError):
            return None, "Please select a valid location on the map."

    return {
        "name": name, "category": category,
        "marking_type": marking_type or None, "status": status, "expires_at": expires_at or None,
        "description": description or None, "location": location or None,
        "latitude": latitude, "longitude": longitude,
    }, None


@app.route("/api/map-markings")
def map_markings_data():
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT {MARKING_COLUMNS} FROM map_markings ORDER BY name COLLATE NOCASE"
        ).fetchall()
    return jsonify({"markings": [marking_to_dict(row) for row in rows]})


@app.route("/api/map-markings", methods=["POST"])
@admin_required
def create_map_marking():
    values, error = parse_marking_payload(request.get_json(silent=True))
    if error:
        return json_error(error, 400)

    with get_connection() as connection:
        cursor = connection.execute(
            """INSERT INTO map_markings (name, category, marking_type, status, expires_at,
                                        description, location, latitude, longitude)
               VALUES (:name, :category, :marking_type, :status, :expires_at,
                       :description, :location, :latitude, :longitude)""",
            values,
        )
        marking = connection.execute(
            f"SELECT {MARKING_COLUMNS} FROM map_markings WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()

    record_audit("map_marking_created", f"Created map marking #{marking['id']} '{values['name']}'.")
    return jsonify({"marking": marking_to_dict(marking)}), 201


@app.route("/api/map-markings/<int:marking_id>", methods=["PUT"])
@admin_required
def update_map_marking(marking_id):
    with get_connection() as connection:
        existing = connection.execute(
            f"SELECT {MARKING_COLUMNS} FROM map_markings WHERE id = ?", (marking_id,)
        ).fetchone()
        if existing is None:
            return json_error("Map marking not found.", 404)

        values, error = parse_marking_payload(request.get_json(silent=True), existing)
        if error:
            return json_error(error, 400)

        values["id"] = marking_id
        connection.execute(
            """UPDATE map_markings
               SET name = :name, category = :category, marking_type = :marking_type,
                   status = :status, expires_at = :expires_at, description = :description,
                   location = :location, latitude = :latitude, longitude = :longitude,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = :id""",
            values,
        )
        marking = connection.execute(
            f"SELECT {MARKING_COLUMNS} FROM map_markings WHERE id = ?", (marking_id,)
        ).fetchone()

    record_audit("map_marking_updated", f"Updated map marking #{marking_id} '{values['name']}'.")
    return jsonify({"marking": marking_to_dict(marking)})


@app.route("/api/map-markings/<int:marking_id>", methods=["DELETE"])
@admin_required
def delete_map_marking(marking_id):
    with get_connection() as connection:
        marking = connection.execute(
            "SELECT name FROM map_markings WHERE id = ?", (marking_id,)
        ).fetchone()
        if marking is None:
            return json_error("Map marking not found.", 404)
        connection.execute("DELETE FROM map_markings WHERE id = ?", (marking_id,))

    record_audit("map_marking_deleted", f"Deleted map marking #{marking_id} '{marking['name']}'.")
    return jsonify({"message": "Map marking deleted successfully."})


@app.route("/api/vulnerability")
@admin_required
def vulnerability_data():
    """Population indicators are an MDRRMO planning resource, so they are
    restricted to administrators."""
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT barangay_name, total_population, senior_count, minor_count,
                      dormitory_student_count, informal_settlement_family_count,
                      vulnerability_level, is_sample
               FROM barangay_vulnerability ORDER BY barangay_name"""
        ).fetchall()
    return jsonify({"barangays": [dict(row) for row in rows]})


# --------------------------------------------------------------------------
# Administrator authentication
# --------------------------------------------------------------------------
# Compared against when the username does not exist, so response time does not
# reveal which usernames are real.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(8))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if "admin_id" in session:
        return redirect(url_for("dashboard"))

    next_page = safe_next(request.values.get("next", ""))
    if request.method == "GET":
        return render_template("admin_login.html", next_page=next_page)

    username = request.form.get("username", "").strip()[:80]
    password = request.form.get("password", "")

    key = (request.remote_addr, username.lower())
    if len(_recent(_login_failures[key], LOGIN_WINDOW)) >= LOGIN_MAX_FAILURES:
        flash("Too many failed sign-in attempts. Please wait 15 minutes and try again.", "error")
        return render_template("admin_login.html", next_page=next_page), 429

    with get_connection() as connection:
        admin = connection.execute(
            "SELECT id, username, password_hash FROM admin_users WHERE username = ?",
            (username,),
        ).fetchone()

    password_ok = check_password_hash(admin["password_hash"] if admin else _DUMMY_HASH, password)
    if admin is None or not password_ok:
        _login_failures[key].append(time.time())
        flash("Invalid username or password.", "error")
        return render_template("admin_login.html", next_page=next_page), 401

    _login_failures.pop(key, None)
    session.clear()  # start a fresh session to prevent session fixation
    session.permanent = True
    session["admin_id"] = admin["id"]
    session["admin_username"] = admin["username"]
    record_audit("admin_login", f"Administrator '{admin['username']}' logged in.")
    return redirect(next_page or url_for("dashboard"))


@app.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    record_audit("admin_logout", f"Administrator '{current_admin_name()}' logged out.")
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("admin_login"))


# --------------------------------------------------------------------------
# Administrator dashboard
# --------------------------------------------------------------------------
REPORTS_PER_PAGE = 20


def _count_by(connection, table, column):
    rows = connection.execute(
        f"SELECT {column} AS key, COUNT(*) AS total FROM {table} GROUP BY {column}"
    ).fetchall()
    return {row["key"]: row["total"] for row in rows}


@app.route("/dashboard")
@admin_required
def dashboard():
    status_filter = request.args.get("status", "")
    type_filter = request.args.get("type", "")
    if status_filter not in STATUS_LABELS:
        status_filter = ""
    if type_filter not in INCIDENT_TYPES:
        type_filter = ""
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    conditions, params = [], []
    if status_filter:
        conditions.append("status = ?")
        params.append(status_filter)
    if type_filter:
        conditions.append("incident_type = ?")
        params.append(type_filter)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with get_connection() as connection:
        by_status = _count_by(connection, "incident_reports", "status")
        by_type = _count_by(connection, "incident_reports", "incident_type")
        by_category = _count_by(connection, "map_markings", "category")

        filtered_total = connection.execute(
            f"SELECT COUNT(*) FROM incident_reports {where}", params
        ).fetchone()[0]
        reports = connection.execute(
            f"""SELECT id, incident_type, incident_location, latitude, longitude,
                       incident_time, time_unknown, photo_path, status, submitted_at
                FROM incident_reports {where}
                ORDER BY submitted_at DESC, id DESC LIMIT ? OFFSET ?""",
            params + [REPORTS_PER_PAGE, (page - 1) * REPORTS_PER_PAGE],
        ).fetchall()

        # Reports submitted per day over the last 14 days (Philippine dates).
        daily_rows = connection.execute(
            """SELECT date(submitted_at, '+8 hours') AS day, COUNT(*) AS total
               FROM incident_reports
               WHERE date(submitted_at, '+8 hours') >= date('now', '+8 hours', '-13 days')
               GROUP BY day"""
        ).fetchall()
        vulnerability = connection.execute(
            """SELECT COUNT(*) AS rows_total,
                      COALESCE(SUM(is_sample), 0) AS sample_rows,
                      COALESCE(SUM(CASE WHEN is_sample = 0 THEN total_population END), 0) AS real_population
               FROM barangay_vulnerability"""
        ).fetchone()

    today = datetime.now(PH_TZ).date()
    daily_lookup = {row["day"]: row["total"] for row in daily_rows}
    daily = []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        daily.append({"label": day.strftime("%d %b"), "count": daily_lookup.get(day.isoformat(), 0)})

    total_reports = sum(by_status.values())
    stats = {
        "total": total_reports,
        "by_status": {key: by_status.get(key, 0) for key in REPORT_STATUSES},
        "markings_total": sum(by_category.values()),
    }
    type_chart = [
        {"key": key, "label": label, "count": by_type.get(key, 0)}
        for key, label in INCIDENT_TYPES.items()
    ]
    category_chart = [
        {"key": key, "label": label, "count": by_category.get(key, 0)}
        for key, label in MARKING_CATEGORIES.items()
    ]

    return render_template(
        "dashboard.html",
        stats=stats,
        type_chart=type_chart,
        category_chart=category_chart,
        daily=daily,
        reports=reports,
        vulnerability=vulnerability,
        status_filter=status_filter,
        type_filter=type_filter,
        page=page,
        pages=max(1, -(-filtered_total // REPORTS_PER_PAGE)),
        filtered_total=filtered_total,
    )


@app.route("/dashboard/report/<int:report_id>")
@admin_required
def report_details(report_id):
    with get_connection() as connection:
        report = connection.execute(
            "SELECT * FROM incident_reports WHERE id = ?", (report_id,)
        ).fetchone()
    if report is None:
        abort(404)
    return render_template("report_details.html", report=report, report_statuses=REPORT_STATUSES)


@app.route("/dashboard/report/<int:report_id>/status", methods=["POST"])
@admin_required
def update_report_status(report_id):
    """Moving a report out of 'pending' is an explicit administrative review."""
    new_status = request.form.get("status", "")
    if new_status not in REPORT_STATUSES:
        flash("Please choose a valid review status.", "error")
        return redirect(url_for("report_details", report_id=report_id))

    with get_connection() as connection:
        report = connection.execute(
            "SELECT status FROM incident_reports WHERE id = ?", (report_id,)
        ).fetchone()
        if report is None:
            abort(404)
        connection.execute(
            """UPDATE incident_reports
               SET status = ?, reviewed_by = ?,
                   reviewed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ?""",
            (new_status, current_admin_name(), report_id),
        )

    record_audit(
        "report_status_changed",
        f"Report #{report_id}: {report['status']} -> {new_status}.",
    )
    flash(f"Report #{report_id} marked as {STATUS_LABELS[new_status].lower()}.", "success")
    return redirect(url_for("report_details", report_id=report_id))


@app.route("/dashboard/vulnerability")
@admin_required
def vulnerability_dashboard():
    with get_connection() as connection:
        barangays = connection.execute(
            "SELECT * FROM barangay_vulnerability ORDER BY barangay_name COLLATE NOCASE"
        ).fetchall()
    return render_template("vulnerability_dashboard.html", barangays=barangays)


@app.route("/admin/incident-photo/<filename>")
@admin_required
def incident_photo(filename):
    """Serve uploaded photos to administrators only."""
    if not PHOTO_NAME_PATTERN.match(filename):
        abort(404)
    response = send_from_directory(UPLOAD_FOLDER, filename, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


# --------------------------------------------------------------------------
# Public incident reporting
# --------------------------------------------------------------------------
def photo_extension_if_valid(upload):
    """Return the file extension if the upload is a real JPG/PNG/WEBP image."""
    name = (upload.filename or "").rsplit(".", 1)
    extension = name[1].lower() if len(name) == 2 else ""
    if extension not in PHOTO_SIGNATURES:
        return None
    header = upload.stream.read(16)
    upload.stream.seek(0)
    if not any(header.startswith(sig) for sig in PHOTO_SIGNATURES[extension]):
        return None
    if extension == "webp" and header[8:12] != b"WEBP":
        return None
    return extension


def validate_report_form(form, files):
    """Return (clean_values, errors). ``errors`` maps field names to messages."""
    errors = {}
    values = {
        "incident_type": form.get("incident_type", "").strip(),
        "description": form.get("description", "").strip(),
        "location": form.get("location", "").strip(),
        "incident_time": form.get("incident_time", "").strip(),
        "time_unknown": form.get("time_unknown") == "yes",
        "latitude": form.get("latitude", "").strip(),
        "longitude": form.get("longitude", "").strip(),
    }

    if values["incident_type"] not in INCIDENT_TYPES:
        errors["incident_type"] = "Please choose the type of incident."
    if not values["description"]:
        errors["description"] = "Please describe what happened."
    elif len(values["description"]) > 1500:
        errors["description"] = "Please keep the description under 1,500 characters."
    if not values["location"]:
        errors["location"] = "Please enter a street, barangay, or landmark."
    elif len(values["location"]) > 250:
        errors["location"] = "Please keep the location under 250 characters."

    if values["time_unknown"]:
        values["incident_time"] = None
    elif not values["incident_time"]:
        errors["incident_time"] = "Enter the date and time, or tick “I don't know the exact time”."
    else:
        try:
            parsed = datetime.strptime(values["incident_time"], "%Y-%m-%dT%H:%M")
            if parsed > datetime.now(PH_TZ).replace(tzinfo=None) + timedelta(minutes=5):
                errors["incident_time"] = "The incident time cannot be in the future."
        except ValueError:
            errors["incident_time"] = "The date or time is not valid."

    latitude = longitude = None
    if values["latitude"] or values["longitude"]:
        try:
            latitude, longitude = parse_coordinates(values["latitude"], values["longitude"])
        except ValueError:
            errors["location_pin"] = "The map pin is invalid. Please place it again."
    values["latitude_value"], values["longitude_value"] = latitude, longitude

    upload = files.get("photo") or files.get("camera_photo")
    values["photo_upload"] = None
    if upload and upload.filename:
        extension = photo_extension_if_valid(upload)
        if extension is None:
            errors["photo"] = "Please attach a real JPG, PNG, or WEBP image."
        else:
            values["photo_upload"] = (upload, extension)

    return values, errors


def new_submission_token():
    """One-time token that stops a refreshed or double-tapped form from saving
    the same report twice."""
    token = uuid.uuid4().hex
    tokens = session.get("report_tokens", [])
    session["report_tokens"] = (tokens + [token])[-5:]
    return token


@app.route("/report", methods=["GET", "POST"])
def report_page():
    if request.method == "GET":
        return render_template("report.html", form={}, errors={}, token=new_submission_token())

    # Throttle abusive volumes of submissions from one address.
    events = _recent(_report_submissions[request.remote_addr], 3600)
    if len(events) >= REPORT_MAX_PER_HOUR:
        return render_template(
            "report.html", form=request.form, errors={"general": "Too many reports were sent from this device. Please try again later."},
            token=new_submission_token(),
        ), 429

    token = request.form.get("submission_token", "")
    tokens = session.get("report_tokens", [])
    if token not in tokens:
        # Already used (refresh/double tap) or expired: do not save again.
        flash("That report was already submitted, or the form expired.", "info")
        return redirect(url_for("report_page"))

    values, errors = validate_report_form(request.form, request.files)
    if errors:
        return render_template(
            "report.html", form=request.form, errors=errors, token=token
        ), 400

    photo_filename = None
    if values["photo_upload"]:
        upload, extension = values["photo_upload"]
        photo_filename = f"{uuid.uuid4().hex}.{extension}"
        upload.save(UPLOAD_FOLDER / photo_filename)

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """INSERT INTO incident_reports
                   (incident_type, description, incident_location, latitude, longitude,
                    incident_time, time_unknown, photo_path, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    values["incident_type"], values["description"], values["location"],
                    values["latitude_value"], values["longitude_value"],
                    values["incident_time"], int(values["time_unknown"]), photo_filename,
                ),
            )
            report_id = cursor.lastrowid
    except Exception:
        if photo_filename:
            (UPLOAD_FOLDER / photo_filename).unlink(missing_ok=True)
        app.logger.exception("Failed to save an incident report.")
        return render_template(
            "report.html", form=request.form,
            errors={"general": "The report could not be saved. Please try again."},
            token=token,
        ), 500

    tokens.remove(token)
    session["report_tokens"] = tokens
    session["last_report_id"] = report_id
    events.append(time.time())
    return redirect(url_for("report_submitted"))


@app.route("/report/submitted")
def report_submitted():
    report_id = session.pop("last_report_id", None)
    if report_id is None:
        return redirect(url_for("report_page"))
    return render_template("report_submitted.html", report_id=report_id)


# --------------------------------------------------------------------------
# Error pages
# --------------------------------------------------------------------------
def error_response(status, title, message):
    if wants_json():
        return json_error(message, status)
    return render_template("error.html", status=status, title=title, message=message), status


@app.errorhandler(404)
def not_found(error):
    return error_response(404, "Page not found", "The page you are looking for does not exist.")


@app.errorhandler(405)
def method_not_allowed(error):
    return error_response(405, "Action not allowed", "That action is not supported for this page.")


@app.errorhandler(413)
def request_too_large(error):
    if request.path == "/report":
        return render_template(
            "report.html", form={}, token=new_submission_token(),
            errors={"photo": "That upload is too large. Photos must be under 10 MB."},
        ), 413
    return error_response(413, "Upload too large", "The upload is too large.")


@app.errorhandler(500)
def server_error(error):
    # Details go to the server log only, never to the visitor.
    return error_response(500, "Something went wrong", "An unexpected error occurred. Please try again.")


if __name__ == "__main__":
    # Debug mode exposes an interactive console, so it is opt-in only.
    app.run(debug=os.environ.get("TANAW_DEBUG", "").lower() == "true")
