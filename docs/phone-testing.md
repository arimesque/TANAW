# Testing camera and location on a phone (HTTPS)

Browsers only allow the **camera** and **precise location** on secure pages:
`https://...`, or `http://localhost` on the *same* computer. A phone opening
`http://192.168.x.x:5000` is **not** secure, so "Take a photo" falls back to the
file picker and location may be refused. To test on a phone you need an HTTPS
address. The easiest free way is a temporary tunnel.

## Option A (recommended): Cloudflare quick tunnel

No account, no card, about two minutes.

1. Install once (Windows PowerShell): `winget install Cloudflare.cloudflared`
   (macOS: `brew install cloudflared`).
2. Start TANAW as usual in one terminal. Keep `TANAW_HTTPS=true` set so session
   cookies are marked secure:
   ```powershell
   $env:TANAW_SECRET_KEY = "any-long-random-text-for-testing"
   $env:TANAW_HTTPS = "true"
   python app.py
   ```
3. In a second terminal:
   ```powershell
   cloudflared tunnel --url http://127.0.0.1:5000
   ```
4. It prints an address like `https://random-words.trycloudflare.com`. Open that
   on the phone (send it to yourself in chat). Share it with the group while the
   tunnel is running.

Things to know:
* The address changes every time you restart the tunnel, and anyone with the link
  can open your test copy. Use a throw-away database (`TANAW_DATABASE=test.db`),
  never the real `tanaw.db`, and close the tunnel when you finish.
* All visitors appear to come from one address, so the app's limits (10 reports
  per hour, login throttling) are shared. Restart `python app.py` to reset them.
* The laptop must stay on and awake.

## Option B: ngrok

Same idea (`ngrok http 5000`), but it needs a free account and an auth token.

## Option C: temporary deployment

A small deployment on any host with HTTPS (for example a free-tier Render or
Railway service running `waitress-serve app:app`) gives a stable address, but
set `TANAW_SECRET_KEY` and `TANAW_HTTPS=true` there and use a test database.

## What to check on the phone

Use a real phone, **outdoors** for location (laptops have no GPS).

1. **Camera:** Report page, "Take a photo", allow the camera. A live preview
   appears; "Capture" attaches a photo and shows the preview. Submit the form and
   check the photo shows in the admin dashboard. Deny the permission once and
   confirm "Choose from device" appears and works.
2. **Location (report form):** "Use my current location". The message should say
   "Improving accuracy…" for a few seconds, then show the accuracy. Indoors or on a
   laptop you should see the orange warning when it is worse than 100 m.
3. **Location (map):** the locate button on the map behaves the same way.
4. **Zoom:** zoom in until the + button stops. Beyond zoom 19 the picture is only
   enlarged (OpenStreetMap has no tiles beyond 19); confirm the group accepts this.
5. **Boundaries:** after rebuilding the GeoJSON, switch on "Barangay boundaries"
   and check all 21 barangays, smooth outlines, and neighbours that touch.

## Automatic check on a laptop

`tests/browser/device_features.py` runs the same camera, location and zoom checks
in Chromium with a fake camera and fake GPS. It is a quick safety net before each
push, **not** a replacement for the phone check above.
