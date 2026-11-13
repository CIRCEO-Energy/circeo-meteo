"""NOAA GEFS 10 m wind: v12 reforecast (2000-2019) and operational archive (2017-present).

Reforecast: s3://noaa-gefs-retrospective/GEFSv12/reforecast/<YYYY>/<YYYYMMDD>00/<mem>/Days:1-10/
holds one GRIB2 file per variable group (``ugrd_hgt_*``, ``vgrd_hgt_*``: all leads, several
heights). The directory is *listed* rather than guessed, so naming changes are tolerated.

Operational: s3://noaa-gefs-pds/gefs.<YYYYMMDD>/00/atmos/pgrb2ap5/ge{c00,pNN}.t00z.pgrb2a.0p50.fFFF
with ``.idx`` sidecars: only the byte ranges of ``UGRD/VGRD:10 m above ground`` are fetched.

Output: one file per month, dims (init, member, lead, lat, lon).
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr

from .common import CFG, regrid, standardise, write_nc

S3R = "https://noaa-gefs-retrospective.s3.amazonaws.com"
S3O = "https://noaa-gefs-pds.s3.amazonaws.com"
SES = requests.Session()
SES.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=64, max_retries=5))


def _list(bucket_url: str, prefix: str) -> list[str]:
    """Keys under ``prefix`` (S3 ListObjectsV2, anonymous)."""
    import xml.etree.ElementTree as ET
    keys, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        r = SES.get(bucket_url, params=params, timeout=60)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"s": root.tag.split("}")[0].strip("{")}
        keys += [c.find("s:Key", ns).text for c in root.findall("s:Contents", ns)]
        if root.findtext("s:IsTruncated", namespaces=ns) != "true":
            return keys
        token = root.findtext("s:NextContinuationToken", namespaces=ns)


def _download(url: str, dest: Path, byte_ranges: list[tuple[int, int | None]] | None = None):
    with open(dest, "wb") as f:
        if not byte_ranges:
            with SES.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                f.writelines(r.iter_content(1 << 22))
            return
        for a, b in byte_ranges:
            rng = f"bytes={a}-{'' if b is None else b}"
            r = SES.get(url, headers={"Range": rng}, timeout=120)
            r.raise_for_status()
            f.write(r.content)


def _open_uv10(path: Path, var: str) -> xr.DataArray:
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={
        "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10},
        "indexpath": ""})
    name = next(iter(ds.data_vars))
    da = ds[name].rename(var)
    if "step" in da.dims:
        da = da.assign_coords(lead=(da["step"] / np.timedelta64(1, "h")).astype("int16"))
        da = da.swap_dims(step="lead").drop_vars([c for c in ("step", "valid_time")
                                                  if c in da.coords])
    return da


# ------------------------------------------------------------------------------- reforecast
def reforecast_day(day: pd.Timestamp, tmp: Path) -> xr.Dataset:
    D = CFG["datasets"]["gefs_reforecast"]
    lead0, lead1, dl = D["leads_h"]
    members = []
    for mem in D["members"]:
        prefix = f"GEFSv12/reforecast/{day:%Y}/{day:%Y%m%d}00/{mem}/Days:1-10/"
        keys = _list(S3R, prefix)
        das = []
        for var, pat in (("u10", "ugrd_hgt"), ("v10", "vgrd_hgt")):
            key = next(k for k in keys if os.path.basename(k).startswith(pat))
            f = tmp / os.path.basename(key)
            _download(f"{S3R}/{key}", f)
            da = _open_uv10(f, var)
            da = da.sel(lead=[x for x in da["lead"].values if x % dl == 0 and lead0 <= x <= lead1])
            das.append(da.load())
            f.unlink()
        members.append(xr.merge(das, compat="override").expand_dims(member=[mem]))
    ds = standardise(xr.concat(members, "member"))
    ds = regrid(ds, D["res"]).astype("float32")
    return ds.expand_dims(init=[np.datetime64(day, "ns")])


# ------------------------------------------------------------------------------ operational
def _idx_ranges(url: str, wanted: tuple[str, ...]) -> list[tuple[int, int | None]]:
    lines = SES.get(url + ".idx", timeout=60).text.strip().splitlines()
    offs = [int(ln.split(":")[1]) for ln in lines]
    out = []
    for i, ln in enumerate(lines):
        if any(w in ln for w in wanted):
            out.append((offs[i], offs[i + 1] - 1 if i + 1 < len(offs) else None))
    return out


def oper_day(day: pd.Timestamp, tmp: Path) -> xr.Dataset:
    D = CFG["datasets"]["gefs_oper"]
    lead0, lead1, dl = D["leads_h"]
    mems = ["c00"] + [f"p{i:02d}" for i in range(1, D["members"] + 1)]
    wanted = (":UGRD:10 m above ground:", ":VGRD:10 m above ground:")

    def one(mem_lead):
        mem, lead = mem_lead
        url = f"{S3O}/gefs.{day:%Y%m%d}/00/atmos/pgrb2ap5/ge{mem}.t00z.pgrb2a.0p50.f{lead:03d}"
        f = tmp / f"{mem}_{lead:03d}.grib2"
        _download(url, f, _idx_ranges(url, wanted))
        ds = xr.open_dataset(f, engine="cfgrib", backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10},
            "indexpath": ""}).load()
        f.unlink()
        return mem, lead, ds[["u10", "v10"]].drop_vars(
            [c for c in ("step", "valid_time", "time", "heightAboveGround", "number")
             if c in ds.coords])

    jobs = [(m, ld) for m in mems for ld in range(lead0, lead1 + 1, dl)]
    with cf.ThreadPoolExecutor(32) as ex:
        res = list(ex.map(one, jobs))
    by_mem = {}
    for mem, lead, ds in res:
        by_mem.setdefault(mem, []).append(ds.expand_dims(lead=[np.int16(lead)]))
    ds = xr.concat([xr.concat(v, "lead").sortby("lead").expand_dims(member=[m])
                    for m, v in by_mem.items()], "member")
    ds = regrid(standardise(ds), D["res"]).astype("float32")
    return ds.expand_dims(init=[np.datetime64(day, "ns")])


def build_month(kind: str, year: int, month: int, out: Path,
                days: list[int] | None = None) -> list[Path]:
    D = CFG["datasets"]["gefs_reforecast" if kind == "reforecast" else "gefs_oper"]
    first = pd.Timestamp(year, month, 1)
    sel = days
    days = [d for d in pd.date_range(first, first + pd.offsets.MonthEnd(0), freq="D")
            if not sel or d.day in sel]
    parts = []
    with tempfile.TemporaryDirectory() as td:
        for day in days:
            try:
                fn = reforecast_day if kind == "reforecast" else oper_day
                parts.append(fn(day, Path(td)))
                print(f"gefs {kind} {day:%Y-%m-%d} ok", flush=True)
            except Exception as exc:  # noqa: BLE001 - a missing day must not lose the month
                print(f"gefs {kind} {day:%Y-%m-%d} MISSING: {exc!r}", flush=True)
    if not parts:
        return []
    ds = xr.concat(parts, "init")
    tag = f"{year}{month:02d}" + (f"_d{sel[0]:02d}" if sel else "")
    name = f"gefs_{kind}_uv10_1p0_{tag}.nc"
    return [write_nc(ds, out / name, {"source": D["source"], "licence": D["licence"],
                                      "attribution": "NOAA/NCEP GEFS (unaltered values, "
                                                     "regridded to 1 deg box means)"})]
