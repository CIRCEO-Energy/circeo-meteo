"""Fast, parallel reader of ECMWF open data on the AWS mirror (index-based HTTP byte ranges).

The ``ecmwf-opendata`` client fetches fields one by one; for 41 steps x 21 ENS members that is
~1700 sequential requests per run (≈ 30 min per day). This module reads the JSON ``.index``
sidecar of each step file, keeps only the wanted fields (10u/10v, chosen members), merges
adjacent byte ranges and downloads all steps in parallel.

File layout (https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com):
``<yyyymmdd>/<HH>z/<model>/<resol>/<stream>/<yyyymmddHH>0000-<step>h-<stream>-<type>.grib2``
with ``model`` in {ifs, aifs-single} (older: aifs, or no model dir with resol ``0p4-beta``),
``type`` = ``fc`` for oper / AIFS, ``ef`` for the ENS (control ``cf`` + perturbed ``pf``).
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from urllib3.util.retry import Retry
import xarray as xr

BASE = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
SES = requests.Session()
RETRY = Retry(total=8, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
              allowed_methods=("GET", "HEAD"), respect_retry_after_header=True)
SES.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=32, max_retries=RETRY))
LAYOUTS = {  # product -> candidate (model dir, resol, stream, file type)
    "oper": [("ifs", "0p25", "oper", "fc"), ("", "0p4-beta", "oper", "fc")],
    "aifs": [("aifs-single", "0p25", "oper", "fc"), ("aifs", "0p25", "oper", "fc")],
    "enfo": [("ifs", "0p25", "enfo", "ef"), ("", "0p4-beta", "enfo", "ef")],
}


def _url(day: pd.Timestamp, hh: int, layout, step: int) -> str:
    model, resol, stream, ftype = layout
    mdir = f"{model}/" if model else ""
    stamp = f"{day:%Y%m%d}{hh:02d}0000"
    return (f"{BASE}/{day:%Y%m%d}/{hh:02d}z/{mdir}{resol}/{stream}/"
            f"{stamp}-{step}h-{stream}-{ftype}.grib2")


def _index(url: str) -> list[dict] | None:
    r = SES.get(url[:-6] + ".index", timeout=60)
    if r.status_code in (403, 404):
        return None
    r.raise_for_status()
    return [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]


def _ranges(entries: list[dict], members: int | None) -> list[tuple[int, int]]:
    keep = []
    for e in entries:
        if e.get("param") not in ("10u", "10v"):
            continue
        if members is not None and e.get("type") == "pf" and int(e.get("number", 0)) > members:
            continue
        keep.append((int(e["_offset"]), int(e["_offset"]) + int(e["_length"]) - 1))
    keep.sort()
    merged = []
    for a, b in keep:
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def _get(url: str, ranges, dest: Path) -> None:
    with open(dest, "wb") as f:
        for a, b in ranges:
            r = SES.get(url, headers={"Range": f"bytes={a}-{b}"}, timeout=120)
            r.raise_for_status()
            f.write(r.content)


def find_layout(product: str, day: pd.Timestamp, hh: int):
    for lay in LAYOUTS[product]:
        if _index(_url(day, hh, lay, 0)) is not None:
            return lay
    return None


def fetch_run(product: str, day: pd.Timestamp, hh: int, steps: list[int],
              members: int | None = None) -> xr.Dataset:
    """u10/v10 of one run as an xarray Dataset (dims [member,] lead, latitude, longitude)."""
    lay = find_layout(product, day, hh)
    if lay is None:
        raise FileNotFoundError(f"{product} {day:%Y-%m-%d} {hh:02d}z not on the AWS mirror")
    with tempfile.TemporaryDirectory() as td:
        def one(step):
            url = _url(day, hh, lay, step)
            idx = _index(url)
            if idx is None:
                return None
            f = Path(td) / f"s{step:03d}.grib2"
            _get(url, _ranges(idx, members), f)
            return step, f
        with cf.ThreadPoolExecutor(8) as ex:
            got = [g for g in ex.map(one, steps) if g is not None]
        if not got:
            raise FileNotFoundError("no step available")
        parts = []
        for step, f in sorted(got):
            if product == "enfo":
                cfd = xr.open_dataset(f, engine="cfgrib", backend_kwargs={
                    "indexpath": "", "filter_by_keys": {"dataType": "cf"}}).load()
                pfd = xr.open_dataset(f, engine="cfgrib", backend_kwargs={
                    "indexpath": "", "filter_by_keys": {"dataType": "pf"}}).load()
                cfd = cfd.drop_vars("number", errors="ignore").expand_dims(member=[0])
                pfd = pfd.rename(number="member")
                ds = xr.concat([cfd[["u10", "v10"]], pfd[["u10", "v10"]]], "member")
            else:
                ds = xr.open_dataset(f, engine="cfgrib", backend_kwargs={"indexpath": ""}).load()
                ds = ds[["u10", "v10"]]
            ds = ds.drop_vars([c for c in ("step", "valid_time", "time", "heightAboveGround",
                                           "surface", "number") if c in ds.coords])
            parts.append(ds.expand_dims(lead=[np.int16(step)]))
    return xr.concat(parts, "lead")
