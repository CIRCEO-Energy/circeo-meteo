"""IBTrACS v04r01 best tracks of the North Atlantic (NOAA NCEI), 1995 onwards, as one NetCDF file.

Observation dimension ``obs`` (6-hourly and intermediate fixes): storm id, name, time, lat, lon,
USA agency maximum sustained wind [kt] and Saffir-Simpson category, WMO wind [kt], radius of maximum
wind and of 34 / 50 / 64 kt winds (max over quadrants) [nmi] when reported.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd
import requests

URL = ("https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/"
       "v04r01/access/csv/ibtracs.NA.list.v04r01.csv")


def build(out: Path, first_year: int = 1995) -> list[Path]:
    import xarray as xr
    r = requests.get(URL, timeout=600)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), skiprows=[1], low_memory=False, keep_default_na=False)
    df = df[pd.to_numeric(df["SEASON"], errors="coerce") >= first_year]
    num = lambda c: pd.to_numeric(df[c], errors="coerce").to_numpy(dtype="float32", na_value=np.nan)  # noqa: E731
    rmax = lambda p: np.nanmax(np.stack([num(f"USA_{p}_{q}") for q in ("NE", "SE", "SW", "NW")]), 0)  # noqa: E731
    ds = xr.Dataset(
        {"sid": ("obs", np.array(df["SID"].tolist(), dtype="U")), "name": ("obs", np.array(df["NAME"].tolist(), dtype="U")),
         "lat": ("obs", num("LAT")), "lon": ("obs", num("LON")), "usa_wind": ("obs", num("USA_WIND")),
         "usa_sshs": ("obs", num("USA_SSHS")), "wmo_wind": ("obs", num("WMO_WIND")),
         "usa_rmw": ("obs", num("USA_RMW")), "r34": ("obs", rmax("R34")), "r50": ("obs", rmax("R50")),
         "r64": ("obs", rmax("R64"))},
        coords={"time": ("obs", pd.to_datetime(df["ISO_TIME"]).to_numpy(dtype="datetime64[ns]"))},
        attrs={"source": URL, "licence": "NOAA NCEI IBTrACS v04r01 (public domain, cite Knapp et al. 2010)",
               "units": "wind kt, radii nmi"})
    f = out / f"ibtracs_na_{first_year}_{int(pd.to_numeric(df["SEASON"]).max())}.nc"
    ds.to_netcdf(f)
    print(f"{f.name}: {ds.sizes['obs']} fixes, {len(set(ds['sid'].values))} storms", flush=True)
    return [f]
