# Barangay boundary data

`bacolor_barangays.geojson` holds the 21 barangays of Bacolor, Pampanga.

* **Source:** PSA / NAMRIA barangay boundaries (PSGC 2023), via the
  `barangay-boundaries-repository` v2026.4.13.0 release (national file, 42,026 features).
* **Known limitation:** that release was simplified at a tolerance of 0.005 degrees
  (about 500 m), so each barangay has only 4-9 vertices and looks angular.
  The positions are correct (verified against the town centre and a sample report)
  but the outlines are generalised. They are for visual reference only.
* **Fix:** run `scripts/build_barangay_boundaries.py` with the full-resolution NAMRIA
  shapefile (see the script's docstring). It now defaults to `--tolerance 0` (no
  simplification) so neighbouring barangays keep exactly the same shared border. Do not hand-draw or smooth these shapes.
* Keep the attribution "PSA / NAMRIA" wherever the boundaries are shown (the map does this).
