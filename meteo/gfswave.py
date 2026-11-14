"""NOAA GFS-Wave (WAVEWATCH III coupled to GFS v16+, since 2021-03-22), global 0.25 deg grid.

s3://noaa-gfs-bdp-pds/gfs.<YYYYMMDD>/<HH>/wave/gridded/gfswave.t<HH>z.global.0p25.f<FFF>.grib2
(+ ``.idx``). Only the wanted messages are fetched (HTTP byte ranges). Output: one file per month,
dims (init, lead, lat, lon), wave directions *coming from* (degrees clockwise from north).
"""
from __future__ import annotations

import concurrent.futures as cf
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .common import CFG, cut_box, expand_steps, regrid, res_tag, standardise, write_nc
from .gefs import SES, _download

BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
# idx pattern -> storage name: total sea (height, peak period, peak direction) and first swell
# partition (height, direction). Kept small: 9 fields x 2 runs would be 66 MB per day.
WANTED = {":HTSGW:surface:": "swh", ":PERPW:surface:": "pp1d", ":DIRPW:surface:": "dirpw",
          ":SWELL:1 in sequence:": "shts", ":SWDIR:1 in sequence:": "mdts"}


def _one(day: pd.Timestamp, hh: int, step: int, tmp: Path) -> xr.Dataset | None:
    url = (f"{BASE}/gfs.{day:%Y%m%d}/{hh:02d}/wave/gridded/"
           f"gfswave.t{hh:02d}z.global.0p25.f{step:03d}.grib2")
    r = SES.get(url + ".idx", timeout=60)
    if r.status_code != 200:
        return None
    lines = r.text.strip().splitlines()
    offs = [int(ln.split(":")[1]) for ln in lines]
    out = {}
    for i, ln in enumerate(lines):
        name = next((v for k, v in WANTED.items() if k in ln), None)
        if name is None:
            continue
        f = tmp / f"{hh}_{step}_{name}.grib2"
        rng = [(offs[i], offs[i + 1] - 1 if i + 1 < len(offs) else None)]
        ds = None
        for _attempt in range(3):  # truncated transfers (PrematureEndOfFile) happen: re-fetch
            try:
                _download(url, f, rng)
                ds = xr.open_dataset(f, engine="cfgrib", backend_kwargs={"indexpath": ""}).load()
                break
            except Exception as exc:  # noqa: BLE001  (gribapi PrematureEndOfFile, EOFError, ...)
                print(f"  gfs_wave {day:%Y-%m-%d} {hh:02d}z f{step:03d} {name}: {exc!r:.100}, "
                      f"re-fetch", flush=True)
        f.unlink(missing_ok=True)
        if ds is None:
            continue
        var = next(iter(ds.data_vars))
        da = ds[var].drop_vars([c for c in ds[var].coords
                                if c not in ("latitude", "longitude")])
        out[name] = da
    if not out:
        return None
    ds = cut_box(standardise(xr.Dataset(out)), pad=0.5)
    return ds.expand_dims(lead=[np.int16(step)])


def build_month(year: int, month: int, out: Path, days: list[int] | None = None) -> list[Path]:
    D = CFG["datasets"]["gfs_wave"]
    steps = expand_steps(D["steps"])
    first = pd.Timestamp(year, month, 1)
    dates = [d for d in pd.date_range(first, first + pd.offsets.MonthEnd(0), freq="D")
             if not days or d.day in days]
    parts = []
    for d in dates:
        for hh in D["runs"]:
            with tempfile.TemporaryDirectory() as td, cf.ThreadPoolExecutor(8) as ex:
                got = [g for g in ex.map(_one, [d] * len(steps), [hh] * len(steps), steps,
                                         [Path(td)] * len(steps)) if g is not None]
            if not got:
                print(f"gfs_wave {d:%Y-%m-%d} {hh:02d}z MISSING", flush=True)
                continue
            ds = regrid(xr.concat(got, "lead").sortby("lead"), D["res"]).astype("float32")
            parts.append(ds.expand_dims(init=[np.datetime64(d + pd.Timedelta(hours=hh), "ns")]))
            print(f"gfs_wave {d:%Y-%m-%d} {hh:02d}z ok ({len(got)} steps)", flush=True)
    if not parts:
        return []
    tag = f"{year}{month:02d}" + (f"_d{days[0]:02d}" if days else "")
    ds = xr.concat(parts, "init", join="outer")
    return [write_nc(ds, out / f"gfs_wave_{res_tag(D['res'])}_{tag}.nc",
                     {"source": D["source"], "licence": D["licence"],
                      "attribution": "NOAA/NCEP GFS-Wave (WAVEWATCH III), cropped; "
                                     "not endorsed by NOAA"})]
