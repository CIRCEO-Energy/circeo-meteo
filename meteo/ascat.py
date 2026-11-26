"""ASCAT 10 m winds (MetOp-B / MetOp-C), daily 0.25-deg grids of the ascending and descending passes with
the time of observation of each cell (NOAA AOML CoastWatch ERDDAP, public). Independent of any NWP model:
used to verify forecasts and reanalyses at the time of the overpass.

One file per satellite and month over the dataset box:
ascat_<sat>_0p25_<YYYYMM>.nc  with  ws, u, v, t_obs (UTC, datetime64) and dims (pass: asc/desc, day, lat, lon).
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr

from .common import CFG

# AOML "ASCAT_WINDS_METOP_<sat>_AOML" products, served by the NOAA UAF ERDDAP (upwell)
IDS = {"B": "noaa_aoml_8c7c_16cc_8cde"}
SRC = "https://upwell.pfeg.noaa.gov/erddap/griddap/{id}.nc"
VARS = ["WSPD", "uwnd", "vwnd", "Time", "Rain_Flag"]


def _get(url, f, tries=4):
    for k in range(tries):
        try:
            with requests.get(url, timeout=900, stream=True,
                              headers={"User-Agent": "circeo-meteo/1.0 (research; github.com/CIRCEO-Energy/circeo-meteo)"}) as r:
                if r.status_code in (403, 404):
                    print(r.status_code, r.headers.get("server"), r.text[:300].replace("\n", " "), flush=True)
                if r.status_code == 404:
                    print("404:", url[:160], "->", r.text[:400].replace("\n", " "), flush=True)
                    return False
                r.raise_for_status()
                with open(f, "wb") as fh:
                    for ch in r.iter_content(1 << 20):
                        fh.write(ch)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"retry {k + 1}: {exc!r}"[:200], flush=True)
            time.sleep(20 * (k + 1))
    raise RuntimeError(url)


def build_month(year: int, month: int, out: Path) -> list[Path]:
    D = CFG["datasets"]["ascat"]
    B = D["box"]
    t0 = pd.Timestamp(year, month, 1)
    t1 = t0 + pd.offsets.MonthEnd(1)
    files = []
    for sat in D["satellites"]:
        parts = []
        for pas in ("Ascending", "Descending"):
            q = ",".join(f"{v}_{pas}[({t0:%Y-%m-%d}T00:00:00Z):1:({t1:%Y-%m-%d}T23:59:59Z)]"
                         f"[({B['lat_min']}):1:({B['lat_max']})][({B['lon_min']}):1:({B['lon_max']})]" for v in VARS)
            tmp = out / f"_ascat_{sat}_{pas}.nc"
            if sat not in IDS or not _get(SRC.format(id=IDS[sat]) + "?" + q, tmp):
                print(f"{sat}: not available", flush=True)
                parts = []
                break
            with xr.open_dataset(tmp) as d:
                d = d.load()
            tmp.unlink()
            day = d["time"].values.astype("datetime64[D]")
            tod = d[f"Time_{pas}"].values.astype("float64")
            tod = np.where(tod > 48, tod, tod * 3600.0)  # seconds of day (the metadata says hours)
            t_obs = day[:, None, None].astype("datetime64[s]") + np.where(np.isfinite(tod), tod, 0).astype("int64").astype("timedelta64[s]")
            ok = np.isfinite(d[f"WSPD_{pas}"].values) & (np.nan_to_num(d[f"Rain_Flag_{pas}"].values) == 0)
            parts.append(xr.Dataset(
                {"ws": (("day", "lat", "lon"), np.where(ok, d[f"WSPD_{pas}"].values, np.nan).astype("float32")),
                 "u": (("day", "lat", "lon"), np.where(ok, d[f"uwnd_{pas}"].values, np.nan).astype("float32")),
                 "v": (("day", "lat", "lon"), np.where(ok, d[f"vwnd_{pas}"].values, np.nan).astype("float32")),
                 "t_obs": (("day", "lat", "lon"), np.where(ok, t_obs, np.datetime64("NaT")).astype("datetime64[ns]"))},
                coords={"day": day.astype("datetime64[ns]"), "lat": d["latitude"].values.astype("float32"),
                        "lon": d["longitude"].values.astype("float32")}))
        if not parts:
            continue
        ds = xr.concat(parts, pd.Index(["asc", "desc"], name="pass"))
        ds.attrs.update(source=SRC.format(id=IDS[sat]), licence=D["licence"], satellite=f"MetOp-{sat}",
                        note="rain-flagged cells removed; t_obs = time of the overpass (UTC)")
        f = out / f"ascat_{sat.lower()}_0p25_{year}{month:02d}.nc"
        enc = {k: {"zlib": True, "complevel": 4} for k in ("ws", "u", "v", "t_obs")}
        ds.to_netcdf(f, encoding=enc)
        print(f"{f.name}: {int(np.isfinite(ds['ws']).sum())} wind cells", flush=True)
        files.append(f)
    return files
