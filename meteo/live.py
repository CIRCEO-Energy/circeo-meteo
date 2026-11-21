"""Live chain: the latest ECMWF AIFS run (open data, AWS mirror), as soon as it is complete, for real-time routing.

Finds the most recent 00/06/12/18 UTC run whose last wanted step is published, fetches u10/v10 on the archive box,
regridded like the monthly archive, and writes

    <out>/ecmwf_aifs_live_uv10_0p25_<YYYYMMDDHH>.nc   (dims init=1, lead, lat, lon; same as the archive)
    <out>/latest.json                                 {"init": ..., "file": ..., "product": "aifs", ...}

With --ens, also the latest AIFS-ENS 00/12 UTC run (0.5 deg, --members members): ecmwf_aifsens_live_uv10_0p5_<..>.nc
Publication (workflow live.yml of circeo-meteo-forecasts): release ``ecmwf-aifs-live``, last --keep runs kept.

    python -m meteo.live --out live [--max-step 240] [--ens --members 50]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .common import BOX, CFG, regrid, write_nc
from .ecmwf_fetch import LAYOUTS, _url, fetch_run


def _exists(url: str) -> bool:
    """Single probe of the .index sidecar, no retries (a run not yet published answers 404 or 5xx)."""
    import requests
    for k in range(4):  # 404: not published; 5xx (S3 "slow down"): short back-off, then give up
        try:
            r = requests.get(url[:-6] + ".index", timeout=30)
            if r.status_code in (200, 403, 404):
                return r.status_code == 200
            print(f"  probe {r.status_code} {url.rsplit('/', 1)[-1]}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  probe failed {url.rsplit('/', 1)[-1]}: {exc!r}"[:200], flush=True)
        time.sleep(10 * (k + 1))
    return False


def find_layout(product, day, hh):
    for lay in LAYOUTS[product]:
        if _exists(_url(day, hh, lay, 0)):
            return lay
    return None

ATTR = "Generated using ECMWF open data (CC-BY-4.0), latest run; real-time chain of CIRCEO HORIZON"


def latest_run(product: str, now: pd.Timestamp, max_step: int, cycles=(0, 6, 12, 18), back: int = 6):
    """(day, hh, layout) of the newest run whose step max_step is published, looking back `back` cycles."""
    t = now.floor("6h")
    for _ in range(back):
        if t.hour in cycles:
            day, hh = t.normalize(), t.hour
            try:  # a run not yet published can answer with repeated 5xx (retries exhausted): treat as missing
                lay = find_layout(product, day, hh)
                if lay is not None and _exists(_url(day, hh, lay, max_step)):
                    return day, hh
            except Exception as exc:  # noqa: BLE001
                print(f"{product} {day:%Y-%m-%d} {hh:02d}z: not available ({type(exc).__name__})", flush=True)
        t -= pd.Timedelta(hours=6)
    return None


MIRRORS = ("https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com", "https://data.ecmwf.int/forecasts")


def build(product: str, out: Path, max_step: int, members: int | None = None) -> Path | None:
    from . import ecmwf_fetch
    cfg = CFG["datasets"]["ecmwf_open"]["products"][product]
    cycles = (0, 12) if product == "aifs_ens" else (0, 6, 12, 18)
    found = None
    for base in MIRRORS:  # AWS mirror first; ECMWF's own server when AWS throttles (persistent 503)
        ecmwf_fetch.BASE = base
        found = latest_run(product, pd.Timestamp.utcnow().tz_localize(None), max_step, cycles)
        if found is not None:
            print(f"{product}: source {base}", flush=True)
            break
    if found is None:
        print(f"{product}: no complete run in the last cycles", flush=True)
        return None
    day, hh = found
    init = day + pd.Timedelta(hours=hh)
    tag = "aifsens" if product == "aifs_ens" else "aifs"
    res = cfg["res"]
    name = f"ecmwf_{tag}_live_uv10_{'0p25' if res == 0.25 else '0p5'}_{init:%Y%m%d%H}.nc"
    f = out / name
    if f.exists():
        return f
    t0 = time.time()
    steps = list(range(0, max_step + 1, 6))
    ds = fetch_run(product, day, hh, steps, members if product == "aifs_ens" else None)
    ds = regrid(ds, res).astype("float32").expand_dims(init=[np.datetime64(init, "ns")])
    write_nc(ds, f, {"source": "ECMWF open data (AWS mirror)", "licence": CFG["datasets"]["ecmwf_open"]["licence"],
                     "attribution": ATTR})
    print(f"{product} {init:%Y-%m-%d %H}z: {dict(ds.sizes)} in {time.time() - t0:.0f}s -> {name}", flush=True)
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="live")
    ap.add_argument("--max-step", type=int, default=240)
    ap.add_argument("--ens", action="store_true")
    ap.add_argument("--members", type=int, default=50)
    ap.add_argument("--lat-min", type=float, default=5.0, help="south edge (HORIZON NA+ zone: trade winds from 5N)")
    a = ap.parse_args()
    BOX["lat_min"] = a.lat_min  # in place: cut_box / target_grid read this dict
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = {"generated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%MZ")}
    for prod in ("aifs",) + (("aifs_ens",) if a.ens else ()):
        f = build(prod, out, a.max_step, a.members)
        if f is not None:
            with xr.open_dataset(f) as d:
                meta[prod] = {"file": f.name, "init": str(d["init"].values[0])[:16] + "Z",
                              "leads_h": [int(d["lead"].values[0]), int(d["lead"].values[-1])],
                              "box": [float(d["lon"].min()), float(d["lon"].max()),
                                      float(d["lat"].min()), float(d["lat"].max())]}
    (out / "latest.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta), flush=True)


if __name__ == "__main__":
    main()
