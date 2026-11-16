"""Vérifie le décodage : compare, en des points posés sur la grille, nos valeurs brutes à celles d'Open-Meteo (modèle GFS seul)."""
import datetime as dt, json, sys, urllib.request
import numpy as np
import meteo

cycle = dt.datetime.strptime(json.load(open("public/index.json"))["cycle"], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
fh = 30
valid = cycle + dt.timedelta(hours=fh)
m = meteo.messages(cycle, fh)
pts = [(48.0, 2.0), (40.5, -74.0), (25.0, 55.5), (-34.0, 151.0), (1.5, 104.0), (60.0, 10.5), (35.5, 139.5), (-23.5, -46.5)]
lat = ",".join(str(p[0]) for p in pts); lon = ",".join(str(p[1]) for p in pts)
day = valid.strftime("%Y-%m-%d")
url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&hourly=wind_speed_250hPa,wind_direction_250hPa,geopotential_height_250hPa,"
       "wind_speed_300hPa,geopotential_height_300hPa,wind_speed_200hPa,wind_gusts_10m,cape&wind_speed_unit=kn&timezone=GMT&start_date=%s&end_date=%s&models=gfs_global" % (lat, lon, day, day))
rows = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "serenair-meteo/1.0"}), timeout=60))
print(f"cycle {cycle:%Y%m%d%H}, échéance +{fh} h, heure {valid:%Y-%m-%d %H} UTC")
print("point            | vent 250 (kt) nous / OM | dir 250 nous / OM | alt 250 (m) nous / OM | vent 300 | vent 200 | rafales | cape")
for (la, lo), r in zip(pts, rows):
    j = int(round((90 - la) / 0.5)); i = int(round((lo % 360) / 0.5)) % 720
    k = r["hourly"]["time"].index(valid.strftime("%Y-%m-%dT%H:00"))
    def sp(l): return float(np.hypot(m[f"UGRD:{l} mb"][j, i], m[f"VGRD:{l} mb"][j, i]) * meteo.KT)
    d = (np.degrees(np.arctan2(-m["UGRD:250 mb"][j, i], -m["VGRD:250 mb"][j, i])) + 360) % 360
    h = r["hourly"]
    print(f"{la:6.1f},{lo:7.1f} | {sp(250):6.1f} / {h['wind_speed_250hPa'][k]:6.1f} | {d:5.0f} / {h['wind_direction_250hPa'][k]:5.0f} | {m['HGT:250 mb'][j,i]:7.0f} / {h['geopotential_height_250hPa'][k]:7.0f} | "
          f"{sp(300):5.1f}/{h['wind_speed_300hPa'][k]:5.1f} | {sp(200):5.1f}/{h['wind_speed_200hPa'][k]:5.1f} | {m['GUST:surface'][j,i]*meteo.KT:4.1f}/{h['wind_gusts_10m'][k]:4.1f} | {m['CAPE:surface'][j,i]:5.0f}/{h['cape'][k]:5.0f}")
