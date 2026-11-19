"""NOAA GEFS 10 m wind: v12 reforecast (2000-2019), operational ensemble and control (2017 ->).

* ``gefs_reforecast``: s3://noaa-gefs-retrospective/GEFSv12/reforecast/<YYYY>/<YYYYMMDD>00/<mem>/
  Days:1-10/ holds one GRIB2 file per variable group (``ugrd_hgt_*``, ``vgrd_hgt_*``: all leads,
  several heights). Members and file names are *listed*, not guessed (5 members, 11 on Wednesdays).
* ``gefs_oper``: s3://noaa-gefs-pds/gefs.<YYYYMMDD>/00/atmos/pgrb2ap5/ge{c00,pNN}.t00z.pgrb2a.0p50.fFFF
* ``gefs_ctl``:  .../atmos/pgrb2sp25/gec00.t00z.pgrb2s.0p25.fFFF (control at native 0.25 deg)

For the operational files only the byte ranges of ``UGRD/VGRD:10 m above ground`` are fetched
(``.idx`` sidecars). Output dims (init, member, lead, lat, lon); one file per month or per day
(``file`` in config.yaml).
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr
from urllib3.util.retry import Retry

from .common import CFG, cut_box, expand_steps, regrid, res_tag, retrying, standardise, write_nc

S3R = "https://noaa-gefs-retrospective.s3.amazonaws.com"
S3O = "https://noaa-gefs-pds.s3.amazonaws.com"
SES = requests.Session()
RETRY = Retry(total=10, backoff_factor=2.0, status_forcelist=(429, 500, 502, 503, 504),
              allowed_methods=("GET", "HEAD"), respect_retry_after_header=True)
SES.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=32, max_retries=RETRY))
ATTR = "NOAA/NCEP GEFS (values cropped/regridded; not original NOAA data, not endorsed by NOAA)"


def _list(bucket_url: str, prefix: str) -> list[str]:
    """Keys under ``prefix`` (S3 ListObjectsV2, anonymous)."""
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
            r = SES.get(url, headers={"Range": f"bytes={a}-{'' if b is None else b}"},
                        timeout=120)
            r.raise_for_status()
            f.write(r.content)


def _open10(path: Path) -> xr.Dataset:
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={
        "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}, "indexpath": ""})
    return cut_box(standardise(ds), pad=0.5).load()


def _drop(ds: xr.Dataset) -> xr.Dataset:
    return ds.drop_vars([c for c in ("step", "valid_time", "time", "heightAboveGround",
                                     "number") if c in ds.coords])


# ------------------------------------------------------------------------------- reforecast
def reforecast_day(day: pd.Timestamp, tmp: Path) -> xr.Dataset:
    D = CFG["datasets"]["gefs_reforecast"]
    leads = set(expand_steps(D["leads_h"]))
    keys = _list(S3R, f"GEFSv12/reforecast/{day:%Y}/{day:%Y%m%d}00/")
    mems = sorted({k.split("/")[4] for k in keys if "/Days:1-10/" in k})
    members = []
    for mem in mems:
        das = []
        for var, pat in (("u10", "ugrd_hgt"), ("v10", "vgrd_hgt")):
            key = next(k for k in keys if f"/{mem}/Days:1-10/" in k
                       and os.path.basename(k).startswith(pat))
            f = tmp / os.path.basename(key)
            _download(f"{S3R}/{key}", f)
            ds = _open10(f)
            f.unlink()
            name = next(iter(ds.data_vars))
            da = ds[name].rename(var)
            da = da.assign_coords(lead=(da["step"] / np.timedelta64(1, "h")).astype("int16"))
            da = da.swap_dims(step="lead")
            das.append(_drop(da.sel(lead=sorted(leads & set(da["lead"].values.tolist())))))
        members.append(xr.merge(das, compat="override").expand_dims(member=[mem]))
    ds = xr.concat(members, "member", join="outer")
    return regrid(ds, D["res"]).astype("float32").expand_dims(init=[np.datetime64(day, "ns")])


def reforecast_syn_day(day: pd.Timestamp, tmp: Path) -> xr.Dataset:
    """Control member: MSLP [hPa] and Z500 [gpm] at the 12-hourly leads, 1-deg points of the dataset box."""
    D = CFG["datasets"]["gefs_reforecast_syn"]
    B = D["box"]
    lat = np.round(np.arange(B["lat_min"], B["lat_max"] + 1e-9, D["res"]), 4)
    lon = np.round(np.arange(B["lon_min"], B["lon_max"] + 1e-9, D["res"]), 4)
    leads = set(expand_steps(D["leads_h"]))
    keys = _list(S3R, f"GEFSv12/reforecast/{day:%Y}/{day:%Y%m%d}00/c00/Days:1-10/")
    das = []
    for var, pats, fk, scale in (("mslp", ("pres_msl",), {"typeOfLevel": "meanSea"}, 0.01),
                                 ("z500", ("hgt_pres_abv700mb", "hgt_pres"),
                                  {"typeOfLevel": "isobaricInhPa", "level": 500}, 1.0)):
        key = next((k for p_ in pats for k in keys if os.path.basename(k).startswith(p_ + "_")), None)
        if key is None:
            print(f"{day:%Y-%m-%d}: no {pats[0]} file", flush=True)
            continue
        f = tmp / os.path.basename(key)
        _download(f"{S3R}/{key}", f)
        ds = xr.open_dataset(f, engine="cfgrib", backend_kwargs={"filter_by_keys": fk, "indexpath": ""})
        ds = standardise(ds)
        da = ds[next(iter(ds.data_vars))].sel(lat=lat, lon=lon, method="nearest").load() * scale
        f.unlink()
        da = da.assign_coords(lat=lat.astype("float32"), lon=lon.astype("float32"))
        da = da.assign_coords(lead=(da["step"] / np.timedelta64(1, "h")).astype("int16")).swap_dims(step="lead")
        das.append(_drop(da.sel(lead=sorted(leads & set(da["lead"].values.tolist())))).rename(var)
                   .drop_vars([c for c in ("isobaricInhPa", "meanSea") if c in da.coords], errors="ignore"))
    return xr.merge(das, compat="override").astype("float32").expand_dims(init=[np.datetime64(day, "ns")])


# ------------------------------------------------------------------------------ operational
def _idx_ranges(url: str, wanted: tuple[str, ...]) -> list[tuple[int, int | None]]:
    r = SES.get(url + ".idx", timeout=60)
    r.raise_for_status()
    lines = r.text.strip().splitlines()
    offs = [int(ln.split(":")[1]) for ln in lines]
    return [(offs[i], offs[i + 1] - 1 if i + 1 < len(offs) else None)
            for i, ln in enumerate(lines) if any(w in ln for w in wanted)]


def oper_day(day: pd.Timestamp, tmp: Path, dataset: str = "gefs_oper") -> xr.Dataset:
    D = CFG["datasets"][dataset]
    steps = expand_steps(D["leads_h"])
    if dataset == "gefs_ctl":
        mems, sub, pat = ["c00"], "pgrb2sp25", "ge{m}.t00z.pgrb2s.0p25.f{s:03d}"
    else:
        mems = ["c00"] + [f"p{i:02d}" for i in range(1, D["members"] + 1)]
        sub, pat = "pgrb2ap5", "ge{m}.t00z.pgrb2a.0p50.f{s:03d}"
    wanted = (":UGRD:10 m above ground:", ":VGRD:10 m above ground:")

    def one(job):
        mem, step = job
        url = f"{S3O}/gefs.{day:%Y%m%d}/00/atmos/{sub}/" + pat.format(m=mem, s=step)
        f = tmp / f"{mem}_{step:03d}.grib2"
        _download(url, f, _idx_ranges(url, wanted))
        ds = _drop(_open10(f)[["u10", "v10"]])
        f.unlink()
        return mem, step, ds

    with cf.ThreadPoolExecutor(16) as ex:
        res = list(ex.map(one, [(m, s) for m in mems for s in steps]))
    by_mem: dict[str, list] = {}
    for mem, step, ds in res:
        by_mem.setdefault(mem, []).append(ds.expand_dims(lead=[np.int16(step)]))
    ds = xr.concat([xr.concat(v, "lead").sortby("lead").expand_dims(member=[m])
                    for m, v in sorted(by_mem.items())], "member")
    return regrid(ds, D["res"]).astype("float32").expand_dims(init=[np.datetime64(day, "ns")])


def build_month(kind: str, year: int, month: int, out: Path,
                days: list[int] | None = None) -> list[Path]:
    """kind: 'reforecast' | 'oper' | 'ctl'."""
    dataset = f"gefs_{kind}"
    D = CFG["datasets"][dataset]
    syn = kind == "reforecast_syn"
    first = pd.Timestamp(year, month, 1)
    sel = [d for d in pd.date_range(first, first + pd.offsets.MonthEnd(0), freq="D")
           if not days or d.day in days]
    stem = f"gefs_{kind}_{res_tag(D['res'])}" if syn else f"gefs_{kind}_uv10_{res_tag(D['res'])}"
    attrs = {"source": D["source"], "licence": D["licence"], "attribution": ATTR,
             **({"box": __import__("json").dumps(D["box"])} if "box" in D else {})}
    parts, files = [], []
    with tempfile.TemporaryDirectory() as td:
        for day in sel:
            try:
                ds = (retrying(reforecast_syn_day, day, Path(td)) if syn
                      else retrying(reforecast_day, day, Path(td)) if kind == "reforecast"
                      else retrying(oper_day, day, Path(td), dataset))
                print(f"{dataset} {day:%Y-%m-%d} ok {dict(ds.sizes)}", flush=True)
            except Exception as exc:  # noqa: BLE001 - a missing day must not lose the month
                print(f"{dataset} {day:%Y-%m-%d} MISSING: {exc!r}", flush=True)
                continue
            if D.get("file") == "daily":
                files.append(write_nc(ds, out / f"{stem}_{day:%Y%m%d}.nc", attrs))
            else:
                parts.append(ds)
    if parts:
        tag = f"{year}{month:02d}" + (f"_d{days[0]:02d}" if days else "")
        files.append(write_nc(xr.concat(parts, "init", join="outer"), out / f"{stem}_{tag}.nc",
                              attrs))
    return files
