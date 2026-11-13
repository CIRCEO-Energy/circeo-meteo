"""ECMWF forecasts: HRES 2016-2022 (WeatherBench 2 Zarr) and ECMWF open data (2024-present).

* WB2 HRES: ``gs://weatherbench2/datasets/hres/2016-2022-0012-1440x721.zarr`` (0.25 deg,
  00/12 UTC, leads 0-240 h every 6 h). The bucket LICENSE file is printed and must be accepted
  explicitly (``--accept-licence``): WB2 states that TIGGE-derived datasets are research-only;
  HRES comes from ECMWF's archive (historical data under CC-BY-4.0) but the file is the authority.
* Open data: AWS mirror (s3://ecmwf-forecasts), CC-BY-4.0, read by ``ecmwf_fetch`` (parallel):
  IFS ``oper`` (HRES / ENS control), IFS ``enfo`` (control + N perturbed) and ``aifs-single``.
  only 10u/10v are fetched, from the .index files (HTTP byte ranges).

Output: one file per month and product, dims (init, [member,] lead, lat, lon).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .common import CFG, cut_box, regrid, standardise, write_nc

ATTR = "Adapted from ECMWF forecast data by ECMWF, licensed under CC BY 4.0"


# ------------------------------------------------------------------------------ WB2 HRES
def wb2_licence() -> str:
    import gcsfs
    fs = gcsfs.GCSFileSystem(token="anon")
    for p in ("weatherbench2/datasets/hres/LICENSE", "weatherbench2/LICENSE"):
        if fs.exists(p):
            return fs.cat(p).decode("utf-8", "replace")
    return "(no LICENSE file found)"


def build_wb2_month(year: int, month: int, out: Path, accept: bool) -> list[Path]:
    D = CFG["datasets"]["ecmwf_hres_wb2"]
    text = wb2_licence()
    print("=== WB2 HRES LICENSE ===\n" + text[:3000] + "\n========================", flush=True)
    if not accept:
        raise SystemExit("Re-run with --accept-licence once the licence above is confirmed "
                         "compatible with commercial use.")
    src = xr.open_zarr(D["source"], chunks=None, storage_options={"token": "anon"})
    src = src[["10m_u_component_of_wind", "10m_v_component_of_wind"]].rename(
        {"10m_u_component_of_wind": "u10", "10m_v_component_of_wind": "v10",
         "time": "init", "prediction_timedelta": "lead"})
    t0 = pd.Timestamp(year, month, 1)
    sub = src.sel(init=slice(t0, t0 + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23)))
    lead0, lead1, dl = D["leads_h"]
    leads = sub["lead"].values / np.timedelta64(1, "h")
    sub = sub.isel(lead=np.where((leads % dl == 0) & (leads >= lead0) & (leads <= lead1))[0])
    sub = sub.assign_coords(lead=(sub["lead"] / np.timedelta64(1, "h")).astype("int16"))
    sub = cut_box(standardise(sub), pad=0.5).load()
    ds = regrid(sub, D["res"]).astype("float32")
    return [write_nc(ds, out / f"ecmwf_hres_wb2_uv10_0p5_{year}{month:02d}.nc",
                     {"source": D["source"], "licence": "see LICENSE in the WB2 bucket",
                      "attribution": ATTR})]


# ------------------------------------------------------------------------------ open data
def build_open_month(year: int, month: int, out: Path, products=("oper", "aifs", "enfo"),
                     days: list[int] | None = None) -> list[Path]:
    """Monthly files of ECMWF open data (fast parallel index-based reader, see ecmwf_fetch)."""
    from .ecmwf_fetch import fetch_run
    D = CFG["datasets"]["ecmwf_open"]
    lead0, lead1, dl = D["leads_h"]
    steps = list(range(lead0, lead1 + 1, dl))
    first = pd.Timestamp(year, month, 1)
    dates = [d for d in pd.date_range(first, first + pd.offsets.MonthEnd(0), freq="D")
             if not days or d.day in days]
    files = []
    for prod in products:
        parts = []
        for d in dates:
            for hh in ((0,) if prod == "enfo" else (0, 12)):  # ENS kept at 00 UTC (volume)
                try:
                    ds = fetch_run(prod, d, hh, steps,
                                   D["ens_members"] if prod == "enfo" else None)
                    res = D["res_ens"] if prod == "enfo" else D["res_oper"]
                    ds = regrid(ds, res).astype("float32")  # already standardised/cropped
                    parts.append(ds.expand_dims(
                        init=[np.datetime64(d + pd.Timedelta(hours=hh), "ns")]))
                    print(f"ecmwf {prod} {d:%Y-%m-%d} {hh:02d}z ok", flush=True)
                except Exception as exc:  # noqa: BLE001 - keep the rest of the month
                    print(f"ecmwf {prod} {d:%Y-%m-%d} {hh:02d}z MISSING: {exc!r}", flush=True)
        if parts:
            ds = xr.concat(parts, "init")
            res = "1p0" if prod == "enfo" else "0p5"
            tag = f"{year}{month:02d}" if not days else f"{year}{month:02d}_d{days[0]:02d}"
            files.append(write_nc(ds, out / f"ecmwf_{prod}_uv10_{res}_{tag}.nc",
                                  {"source": "ECMWF open data (AWS mirror)",
                                   "licence": D["licence"], "attribution": ATTR}))
    return files
