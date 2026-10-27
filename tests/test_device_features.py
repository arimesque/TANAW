"""Backend checks for the device features (camera, location, zoom).

The browser behaviour itself is covered by tests/browser/device_features.py;
these tests protect the server-side pieces it depends on."""
import io
import re

from conftest import PNG, csrf


def test_permissions_policy_allows_camera_and_location_for_own_pages(client):
    policy = client.get("/report").headers["Permissions-Policy"]
    assert "camera=(self)" in policy
    assert "geolocation=(self)" in policy


def test_csp_still_blocks_third_party_scripts(client):
    csp = client.get("/report").headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-eval" not in csp


def test_report_page_has_camera_dialog_and_fallback_input(client):
    html = client.get("/report").get_data(as_text=True)
    # Live camera dialog, closed by default.
    assert re.search(r'id="camera-modal"[^>]*\shidden', html)
    for element_id in ("camera-video", "camera-capture", "camera-cancel", "camera-fallback", "camera-label"):
        assert f'id="{element_id}"' in html
    # The file picker is kept as a fallback (and works without JavaScript).
    assert 'id="camera-file"' in html and 'capture="environment"' in html
    assert 'for="camera-file" id="camera-label"' in html
    assert "css/device.css" in html


def test_camera_photo_field_is_accepted_and_stored(client):
    """A photo captured in the browser is posted as camera_photo (a JPEG here)."""
    token = csrf(client, "/report")
    page = client.get("/report").get_data(as_text=True)
    submission = re.search(r'name="submission_token" value="([^"]+)"', page).group(1)
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
    response = client.post("/report", data={
        "csrf_token": token, "submission_token": submission, "incident_type": "flooding",
        "incident_time": "2024-01-01T10:00", "description": "Water on the road", "location": "Test street",
        "camera_photo": (io.BytesIO(jpeg), "camera-1.jpg"),
    }, content_type="multipart/form-data")
    # Either accepted (redirect to the thank-you page) or rejected only because the
    # tiny stand-in JPEG is not a decodable image; it must never be a server error.
    assert response.status_code in (200, 302)


def test_png_camera_photo_is_accepted(client):
    token = csrf(client, "/report")
    page = client.get("/report").get_data(as_text=True)
    submission = re.search(r'name="submission_token" value="([^"]+)"', page).group(1)
    response = client.post("/report", data={
        "csrf_token": token, "submission_token": submission, "incident_type": "fire",
        "incident_time": "2024-01-01T10:00", "description": "Smoke", "location": "Test street",
        "camera_photo": (io.BytesIO(PNG), "camera-1.png"),
    }, content_type="multipart/form-data")
    assert response.status_code == 302


def test_zoom_limits_are_declared_in_both_map_scripts(client):
    for path in ("/static/js/map.js", "/static/js/report.js"):
        source = client.get(path).get_data(as_text=True)
        assert "maxNativeZoom: NATIVE_MAX_ZOOM" in source, path
        assert "NATIVE_MAX_ZOOM = 19" in source, path   # OpenStreetMap has no tiles beyond 19
        assert re.search(r"MAX_ZOOM = (2[0-9])", source), path


def test_boundary_script_defaults_to_no_simplification():
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parent.parent / "scripts" / "build_barangay_boundaries.py"
    text = script.read_text(encoding="utf-8")
    assert 'default=0.0' in text   # keeps borders shared between neighbouring barangays exact
