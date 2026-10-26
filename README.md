# TANAW - Territorial Analysis and Network for Accident Watch

Incident map and reporting system for MDRRMO Bacolor, Pampanga.
Stack: Flask, SQLite, Leaflet/OpenStreetMap, vanilla JavaScript (no build step).

## Run locally (Windows PowerShell)

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Required: signs login sessions. Generate once and keep it private.
$env:TANAW_SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"

python create_admin.py      # only if you need a new administrator
python app.py               # http://127.0.0.1:5000
```
macOS/Linux: use `source venv/bin/activate` and `export TANAW_SECRET_KEY=...`.
Keep the same secret key between runs, otherwise administrators are logged out.

Your existing `tanaw.db` is used as-is; on start-up `database.py` applies small,
repeatable, non-destructive migrations (recorded in `schema_migrations`).

Optional environment variables: `TANAW_DATABASE`, `TANAW_UPLOAD_DIR`,
`TANAW_HTTPS=true` (secure cookies, set when served over HTTPS), `TANAW_DEBUG=true` (development only).
For production use a real WSGI server (for example `waitress-serve --listen=0.0.0.0:8000 app:app`) behind HTTPS.

## Project layout
`app.py` routes and validation · `database.py` schema + migrations · `templates/` pages ·
`static/css|js` interface · `static/vendor/leaflet` Leaflet 1.9.4 (vendored, no CDN) ·
`static/icons` + `static/js/icons.js` Lucide icons (ISC) · `static/data` boundaries ·
`scripts/build_barangay_boundaries.py` · `tests/`.

## Tests
```
pip install -r requirements-dev.txt
python -m pytest -q                    # backend: 31 tests
cd tests/frontend && npm install && npm test   # browser-DOM tests (needs the app running; see seed.py)
```
Frontend tests run map.js/report.js in jsdom against a live server on port 5055
(`python tests/frontend/seed.py` seeds a throwaway database; start the app with
`TANAW_DATABASE` pointing to it). They do not replace checking in a real browser.

## Security notes
Admin writes are enforced server-side (401 JSON for API calls), CSRF tokens on every
state-changing request, login throttling (in-memory), photo uploads checked by content and
served only to administrators from a private folder, strict Content-Security-Policy.
Never commit `tanaw.db`, `uploads/`, or your secret key (see `.gitignore`).
