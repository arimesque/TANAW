"""Real-browser checks for the device features: live camera, best-fix location,
extra zoom. Needs the app running and Playwright with Chromium:

    pip install playwright && playwright install chromium
    python app.py                                  # in another terminal
    python tests/browser/device_features.py [http://127.0.0.1:5000]

Chromium is started with a fake camera and a fake GPS, so no real hardware is
needed. This does NOT replace testing on a real phone over HTTPS (see
docs/phone-testing.md): real cameras and real GPS behave differently.
"""
import io
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
passed = failed = 0


def check(condition, label):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
    print(("  PASS " if condition else "  FAIL ") + label)


def wait_until(page, expression, timeout=8000):
    """Poll a JS expression with evaluate(). wait_for_function() cannot be used
    because the app's strict Content-Security-Policy blocks string evaluation."""
    end = time.time() + timeout / 1000
    while time.time() < end:
        if page.evaluate("() => !!(" + expression + ")"):
            return
        page.wait_for_timeout(100)
    raise AssertionError("timed out waiting for: " + expression)


def new_page(browser, **ctx):
    context = browser.new_context(**ctx)
    page = context.new_page()
    tile_urls = []
    page.on("request", lambda r: tile_urls.append(r.url) if "tile.openstreetmap.org" in r.url else None)
    page.route("**/tile.openstreetmap.org/**", lambda route: route.abort())  # stay offline-safe
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return context, page, tile_urls, errors


FAKE_CAM = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]


with sync_playwright() as p:
    # ---------------- Camera: works ----------------
    print("Camera (fake device)")
    browser = p.chromium.launch(args=FAKE_CAM)
    context, page, _, errors = new_page(browser, permissions=["camera"], viewport={"width": 390, "height": 800})
    page.goto(BASE + "/report")
    check(page.locator("#camera-modal").is_hidden(), "camera dialog is closed on page load")
    page.click("#camera-label")
    page.wait_for_selector("#camera-modal:not([hidden])")
    wait_until(page, "document.getElementById('camera-video').videoWidth > 0", 8000)
    check(page.locator("#camera-capture").is_enabled(), "capture enabled once the live preview runs")
    check(page.locator("#camera-fallback").is_hidden(), "file-picker fallback button is hidden while the camera works")
    check(page.evaluate("document.getElementById('camera-video').srcObject !== null"), "live stream attached to <video>")
    page.press("#camera-modal", "Escape")
    check(page.locator("#camera-modal").is_hidden(), "Escape closes the dialog")
    check(page.evaluate("document.getElementById('camera-video').srcObject === null"), "camera is released after closing")
    page.click("#camera-label")
    wait_until(page, "!document.getElementById('camera-capture').disabled")
    page.click("#camera-capture")
    page.wait_for_selector("#photo-preview:not([hidden])")
    info = page.evaluate("""() => { const f = document.getElementById('camera-file').files[0];
        return f ? {type: f.type, size: f.size, name: f.name} : null; }""")
    check(info and info["type"] == "image/jpeg" and 0 < info["size"] < 10 * 1024 * 1024, f"captured JPEG attached to the form ({info})")
    check(page.locator("#camera-modal").is_hidden(), "dialog closes after capture")
    page.click("#remove-photo")
    check(page.evaluate("document.getElementById('camera-file').files.length") == 0, "Remove photo clears the captured file")

    # End to end: capture, fill the form, submit, and the server accepts the photo.
    page.click("#camera-label")
    wait_until(page, "!document.getElementById('camera-capture').disabled")
    page.click("#camera-capture")
    page.wait_for_selector("#photo-preview:not([hidden])")
    page.click(".type-option >> nth=1")
    page.fill("#incident-time", "2024-01-01T10:00")
    page.fill("#description", "Browser test report with camera photo")
    page.fill("#location", "Test street")
    page.click("#submit-button")
    page.wait_for_load_state("networkidle")
    check(page.url.endswith("/report/submitted"), f"report with camera photo accepted by the server ({page.url})")
    check(not errors, "no JavaScript errors" + (": " + "|".join(errors) if errors else ""))
    context.close(); browser.close()

    # ---------------- Camera: unavailable -> file picker fallback ----------------
    print("Camera (no device -> fallback)")
    browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream"])  # no fake device
    context, page, _, errors = new_page(browser, permissions=["camera"])
    page.goto(BASE + "/report")
    page.click("#camera-label")
    page.wait_for_selector("#camera-fallback:not([hidden])", timeout=8000)
    check("No camera" in page.inner_text("#camera-status") or "could not" in page.inner_text("#camera-status"), "clear message when no camera exists")
    check(page.locator("#camera-capture").is_disabled(), "capture stays disabled when the camera fails")
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201a5f645400000000049454e44ae426082")
    with page.expect_file_chooser() as chooser:
        page.click("#camera-fallback")
    chooser.value.set_files(files=[{"name": "photo.png", "mimeType": "image/png", "buffer": png}])
    page.wait_for_selector("#photo-preview:not([hidden])")
    check(page.locator("#camera-modal").is_hidden(), "fallback closes the dialog and opens the file picker")
    check(page.evaluate("document.getElementById('camera-file').files.length") == 1, "file chosen through the fallback is attached")
    context.close(); browser.close()

    # ---------------- Camera: permission denied ----------------
    print("Camera (permission denied)")
    # Real browsers reject with NotAllowedError when the person taps "Block".
    browser = p.chromium.launch(args=FAKE_CAM)
    context, page, _, _ = new_page(browser)
    context.add_init_script("""navigator.mediaDevices.getUserMedia = () =>
        Promise.reject(new DOMException('Permission denied', 'NotAllowedError'));""")
    page.goto(BASE + "/report")
    page.click("#camera-label")
    page.wait_for_selector("#camera-fallback:not([hidden])", timeout=8000)
    check("denied" in page.inner_text("#camera-status").lower(), "clear message when permission is denied")
    context.close(); browser.close()

    # ---------------- Location ----------------
    print("Location: good fix (report form)")
    browser = p.chromium.launch()
    context, page, tiles, errors = new_page(browser, permissions=["geolocation"],
                                            geolocation={"latitude": 15.0012, "longitude": 120.6511, "accuracy": 6})
    page.goto(BASE + "/report")
    page.click("#locate-button")
    wait_until(page, "document.getElementById('latitude').value !== ''", 5000)
    wait_until(page, "!document.getElementById('locate-button').disabled", 12000)
    status = page.inner_text("#location-status")
    check(page.input_value("#latitude") == "15.001200", "pin set from the reading")
    check("accuracy" in status and "Warning" not in status, f"good accuracy shows no warning ({status!r})")
    check("is-warning" not in (page.get_attribute("#location-status", "class") or ""), "no warning style for a good fix")
    context.close()

    print("Location: poor fix (laptop-like, 1.5 km)")
    context, page, _, _ = new_page(browser, permissions=["geolocation"],
                                   geolocation={"latitude": 15.0, "longitude": 120.65, "accuracy": 1500})
    page.goto(BASE + "/report")
    t0 = time.time()
    page.click("#locate-button")
    wait_until(page, "!document.getElementById('locate-button').disabled", 15000)
    status = page.inner_text("#location-status")
    check("Warning" in status and "1.5 km" in status, f"poor accuracy shows a warning with the figure ({status!r})")
    check("is-warning" in page.get_attribute("#location-status", "class"), "warning style applied")
    check(5 <= time.time() - t0 <= 12, f"kept listening for the window before settling ({time.time() - t0:.1f}s)")
    context.close()

    print("Location: pin dragged by hand is not overwritten")
    context, page, _, _ = new_page(browser, permissions=["geolocation"],
                                   geolocation={"latitude": 15.0, "longitude": 120.65, "accuracy": 1500})
    page.goto(BASE + "/report")
    page.click("#locate-button")
    wait_until(page, "document.getElementById('latitude').value !== ''")
    page.click("#report-map", position={"x": 40, "y": 40})  # tapping the map takes control
    lat_after_tap = page.input_value("#latitude")
    page.wait_for_timeout(9500)
    check(page.input_value("#latitude") == lat_after_tap, "later GPS readings do not move a hand-placed pin")
    check(page.locator("#locate-button").is_enabled(), "locate button is usable again")
    context.close()

    print("Location: denied")
    context, page, _, _ = new_page(browser)  # no geolocation permission
    page.goto(BASE + "/report")
    page.click("#locate-button")
    wait_until(page, "!document.getElementById('locate-button').disabled", 8000)
    check("denied" in page.inner_text("#location-status"), "denied permission explained")
    context.close()

    print("Location: map page button")
    context, page, _, errors = new_page(browser, permissions=["geolocation"],
                                        geolocation={"latitude": 15.0, "longitude": 120.65, "accuracy": 1500})
    page.goto(BASE + "/map")
    page.click('button[aria-label="Show my location"]')
    wait_until(page, "/Warning/.test(document.getElementById('map-message').textContent)", 15000)
    check(page.locator("#map-message.is-error").count() == 1, "map shows a poor-accuracy warning")
    check(not errors, "no JavaScript errors on the map" + (": " + "|".join(errors) if errors else ""))
    context.close()

    # ---------------- Zoom ----------------
    print("Zoom")
    for path in ("/map", "/report"):
        context, page, tiles, errors = new_page(browser, viewport={"width": 1200, "height": 800})
        page.goto(BASE + path)
        for _ in range(20):   # each click waits for Leaflet's zoom animation to finish
            btn = page.locator(".leaflet-control-zoom-in")
            if "leaflet-disabled" in (btn.get_attribute("class") or ""):
                break
            btn.click()
            page.wait_for_timeout(500)
        page.wait_for_timeout(500)
        zooms = [int(m.group(1)) for u in tiles for m in [re.search(r"openstreetmap\.org/(\d+)/", u)] if m]
        check("leaflet-disabled" in page.locator(".leaflet-control-zoom-in").get_attribute("class"), f"{path}: zoom-in stops at the limit")
        check(zooms and max(zooms) == 19, f"{path}: highest tile requested is level 19 (got {max(zooms) if zooms else None})")
        context.close()
    browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
