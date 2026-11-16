# serenair-meteo

Turbulence-forecast tiles for the Serenair app, rebuilt every 6 hours from NOAA's Global Forecast System (GFS, 0.5°).

A scheduled GitHub Actions job runs `meteo.py`, which downloads only the fields it needs from the public NOAA bucket
(wind and height at 300, 250 and 200 hPa, CAPE, surface gusts), computes four values per grid cell and publishes them
as static files on GitHub Pages:

- `index.json`: model cycle, grid, available hours (UTC);
- `v1/<YYYYMMDDHH>/<ty>_<tx>.bin`: one 30° × 30° tile per hour, four 61 × 61 byte fields (the last row and column
  repeat the neighbouring tile's edge, so four cells can be interpolated from one tile) in this order:
  `shear` (kt per 1,000 ft, × 0.1), `jet` (kt at 250 hPa, × 1), `cape` (J/kg, × 20), `gust` (kt, × 0.5);
  rows north to south, columns eastward from 0°, 255 = missing.

Hours: every 3 h up to 5 days, then every 6 h up to 16 days.

    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python meteo.py --out public --steps 4   # quick try

Data: NOAA GFS, public domain (CC0), from https://registry.opendata.aws/noaa-gfs-pds. NOAA does not endorse this
project; the tiles are derived values, not original NOAA output. A forecast is not safety advice.
