#!/usr/bin/env python3
"""Serenair météo : lit le modèle mondial GFS de la NOAA (domaine public) et publie de petits fichiers statiques
que l'app lit directement. Aucun serveur : une tâche planifiée lance ce script 4 fois par jour.

Ce que l'app demande, par point de la route et par heure :
  - shear : cisaillement vertical du vent entre 300, 250 et 200 hPa, en nœuds pour 1 000 pieds (secousses en croisière) ;
  - jet   : vitesse du vent à 250 hPa, en nœuds ;
  - cape  : énergie des orages, en J/kg ;
  - gust  : rafales au sol, en nœuds (décollage et atterrissage).

Sortie (dossier --out) :
  index.json                  le cycle du modèle, la grille, les heures disponibles
  v1/<AAAAMMJJHH>/<ty>_<tx>.bin   une tuile de 30° × 30° pour une heure UTC : 4 champs de 61 × 61 octets, dans
                                  l'ordre shear, jet, cape, gust ; lignes du nord au sud, colonnes vers l'est ;
                                  la dernière ligne et la dernière colonne répètent le bord de la tuile voisine,
                                  pour interpoler entre 4 mailles sans ouvrir deux tuiles ;
                                  255 = donnée absente ; valeur = octet × échelle (voir index.json).

Source : NOAA GFS, fichiers au demi-degré du dépôt public https://registry.opendata.aws/noaa-gfs-pds (CC0).
    python meteo.py --out public            # dernier cycle complet
    python meteo.py --out public --steps 4  # essai rapide : 4 premières heures seulement
    python meteo.py --quoi https://…        # le dernier cycle est-il déjà publié à cette adresse ? (sortie GitHub Actions)
    python meteo.py --frais public 18       # échoue si le cycle écrit dans public/ a plus de 18 heures
"""
import argparse, datetime as dt, json, os, shutil, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

import eccodes
import numpy as np

BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
UA = {"User-Agent": "serenair-meteo/1.0"}
# Toutes les 3 h jusqu'à 5 jours, puis toutes les 6 h jusqu'à 16 jours : l'app ouvre la prévision 14 jours avant le
# départ, et les 2 jours de marge couvrent la durée du vol et un cycle en retard.
HOURS = list(range(0, 121, 3)) + list(range(126, 385, 6))
LEVELS = (300, 250, 200)
WANT = [f"{v}:{l} mb" for l in LEVELS for v in ("UGRD", "VGRD", "HGT")] + ["CAPE:surface", "GUST:surface"]
NX, NY, TILE = 720, 360, 60  # demi-degré : 720 × 360 mailles, tuiles de 60 mailles de côté
FIELDS = ["shear", "jet", "cape", "gust"]
SCALE = [0.1, 1, 20, 0.5]
KT = 1.943844  # m/s → nœuds
MISSING = 255
# Les téléchargements se font en parallèle ; le décodage, lui, passe un message à la fois (bibliothèque C partagée).
DECODE = threading.Lock()


def get(url, rng=None, tries=4):
    """GET avec reprises. `rng` = (début, fin incluse) pour ne lire qu'un morceau du fichier."""
    headers = dict(UA)
    if rng:
        headers["Range"] = f"bytes={rng[0]}-{rng[1]}"
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            err = e
        except Exception as e:  # réseau, délai
            err = e
        time.sleep(2 * (k + 1))
    raise RuntimeError(f"{url} : {err}")


def exists(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA, method="HEAD"), timeout=30):
            return True
    except Exception:
        return False


def path(cycle, fh):
    return f"{BASE}/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2.0p50.f{fh:03d}"


def latest_cycle(now=None):
    """Dernier cycle (00, 06, 12, 18 UTC) dont la dernière heure utile est déjà en ligne."""
    now = now or dt.datetime.now(dt.timezone.utc)
    c = now.replace(minute=0, second=0, microsecond=0)
    c -= dt.timedelta(hours=c.hour % 6)
    for _ in range(8):
        if exists(path(c, HOURS[-1]) + ".idx"):
            return c
        c -= dt.timedelta(hours=6)
    raise RuntimeError("aucun cycle GFS complet dans les 48 dernières heures")


def messages(cycle, fh):
    """Télécharge et décode les 11 champs utiles d'une heure de prévision. Rend {nom: tableau (361, 720)}."""
    url = path(cycle, fh)
    lines = get(url + ".idx").decode().strip().split("\n")
    starts = [int(l.split(":")[1]) for l in lines]
    out = {}
    for i, l in enumerate(lines):
        p = l.split(":")
        key = f"{p[3]}:{p[4]}"
        if key not in WANT:
            continue
        end = starts[i + 1] - 1 if i + 1 < len(starts) else ""
        raw = get(url, (starts[i], end))
        with DECODE:
            out[key] = decode(raw, key)
    lack = [k for k in WANT if k not in out]
    if lack:
        raise RuntimeError(f"f{fh:03d} : champs absents {lack}")
    return out


def decode(raw, key):
    """Un message GRIB → tableau (361, 720), du nord au sud, depuis le méridien 0 vers l'est. NaN = absent."""
    h = eccodes.codes_new_from_message(raw)
    try:
        ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
        lat0, lon0 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees"), eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees")
        if (ni, nj, lat0, lon0) != (NX, NY + 1, 90.0, 0.0):
            raise RuntimeError(f"grille inattendue pour {key} : {ni}×{nj}, départ {lat0},{lon0}")
        eccodes.codes_set(h, "missingValue", 9.999e20)
        v = eccodes.codes_get_values(h).reshape(nj, ni)
        return np.where(v > 9e19, np.nan, v)
    finally:
        eccodes.codes_release(h)


def fields(m):
    """Les 4 champs de l'app, à partir des champs du modèle. Même calcul que src/lib/forecast.ts."""

    def shear(a, b):
        dv = np.hypot(m[f"UGRD:{a} mb"] - m[f"UGRD:{b} mb"], m[f"VGRD:{a} mb"] - m[f"VGRD:{b} mb"]) * KT
        dz = np.abs(m[f"HGT:{b} mb"] - m[f"HGT:{a} mb"]) * 3.281 / 1000  # milliers de pieds
        return np.where(dz > 0.5, dv / np.maximum(dz, 0.5), 0.0)

    sh = np.maximum(shear(300, 250), shear(250, 200))
    jet = np.hypot(m["UGRD:250 mb"], m["VGRD:250 mb"]) * KT
    return [sh, jet, m["CAPE:surface"], m["GUST:surface"] * KT]


def pack(values):
    """4 champs (361, 720) → octets (4, 361, 721) : la colonne 720 répète la colonne 0 (le globe se referme). 255 = absent."""
    out = np.empty((4, NY + 1, NX + 1), dtype=np.uint8)
    for i, (v, s) in enumerate(zip(values, SCALE)):
        q = np.where(np.isnan(v), MISSING, np.clip(np.rint(v / s), 0, 254)).astype(np.uint8)
        out[i, :, :NX] = q
        out[i, :, NX] = q[:, 0]
    return out


def write_step(root, valid, packed):
    d = os.path.join(root, "v1", f"{valid:%Y%m%d%H}")
    os.makedirs(d, exist_ok=True)
    for ty in range(NY // TILE):
        for tx in range(NX // TILE):
            with open(os.path.join(d, f"{ty}_{tx}.bin"), "wb") as f:
                f.write(packed[:, ty * TILE : (ty + 1) * TILE + 1, tx * TILE : (tx + 1) * TILE + 1].tobytes())


def quoi(url):
    """Pour la tâche planifiée : écrit `cycle=…` et `a_jour=true|false` (le cycle est déjà en ligne à `url`)."""
    cycle = f"{latest_cycle():%Y%m%d%H}"
    try:
        en_ligne = json.loads(get(url.rstrip("/") + "/index.json", tries=2)).get("cycle")
    except Exception:
        en_ligne = None
    print(f"cycle={cycle}")
    print(f"a_jour={'true' if en_ligne == cycle else 'false'}")


def frais(dossier, heures):
    """Échoue si le cycle publié est trop vieux : la NOAA est en retard ou la tâche ne tourne plus."""
    cycle = json.load(open(os.path.join(dossier, "index.json")))["cycle"]
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.strptime(cycle, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)).total_seconds() / 3600
    print(f"cycle {cycle}, {age:.1f} h")
    if age > heures:
        raise RuntimeError(f"le cycle {cycle} a {age:.0f} h (limite {heures:.0f} h)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quoi", metavar="URL")
    ap.add_argument("--frais", nargs=2, metavar=("DOSSIER", "HEURES"))
    ap.add_argument("--out", default="public")
    ap.add_argument("--cycle", help="AAAAMMJJHH (UTC) ; par défaut le dernier cycle complet")
    ap.add_argument("--steps", type=int, help="ne traiter que les N premières heures (essai)")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    if a.quoi:
        return quoi(a.quoi)
    if a.frais:
        return frais(a.frais[0], float(a.frais[1]))

    cycle = dt.datetime.strptime(a.cycle, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc) if a.cycle else latest_cycle()
    hours = HOURS[: a.steps] if a.steps else HOURS
    tmp = a.out + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    print(f"cycle {cycle:%Y-%m-%d %H} UTC, {len(hours)} heures", flush=True)

    def one(fh):
        valid = cycle + dt.timedelta(hours=fh)
        write_step(tmp, valid, pack(fields(messages(cycle, fh))))
        return f"{valid:%Y%m%d%H}"

    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        steps = list(ex.map(one, hours))
    index = {
        "v": 1,
        "source": "NOAA GFS 0.5°",
        "cycle": f"{cycle:%Y%m%d%H}",
        "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "res": 0.5,
        "nx": NX,
        "ny": NY,
        "tile": TILE,
        "edge": 1,
        "fields": FIELDS,
        "scale": SCALE,
        "missing": MISSING,
        "steps": steps,
    }
    with open(os.path.join(tmp, "index.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    # Rien n'est publié à moitié : on ne remplace l'ancien dossier qu'une fois tout écrit.
    shutil.rmtree(a.out, ignore_errors=True)
    os.rename(tmp, a.out)
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(a.out) for f in fs)
    print(f"écrit {len(steps)} heures, {size / 1e6:.1f} Mo, en {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ÉCHEC : {e}", file=sys.stderr)
        sys.exit(1)
