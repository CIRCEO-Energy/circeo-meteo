"""ERA5 hourly 10 m wind from Google's public ARCO-ERA5 Zarr (no account, no queue).

One output file per month: ``era5_uv10_0p25_<YYYYMM>.nc`` (time, lat, lon), native 0.25 deg, hourly.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import xarray as xr

from .common import CFG, cut_box, regrid, res_tag, standardise, write_nc

D = CFG["datasets"]["era5"]
VARS = {"10m_u_component_of_wind": "u10", "10m_v_component_of_wind": "v10"}


def open_arco() -> xr.Dataset:
    ds = xr.open_zarr(D["source"], chunks=None, storage_options={"token": "anon"})
    return ds[list(VARS)].rename(VARS)


def build_year(year: int, out: Path, months: list[int] | None = None) -> list[Path]:
    """One file per month: ``era5_uv10_<res>_<YYYYMM>.nc`` (native 0.25 deg, hourly)."""
    src = open_arco()
    files = []
    for m in months or range(1, 13):
        t0 = pd.Timestamp(year, m, 1)
        t1 = t0 + pd.offsets.MonthBegin(1) - pd.Timedelta(hours=1)
        sub = cut_box(standardise(src.sel(time=slice(t0, t1))), pad=0.5).load()
        ds = regrid(sub, D["res"]).astype("float32")
        f = out / f"era5_uv10_{res_tag(D['res'])}_{year}{m:02d}.nc"
        files.append(write_nc(ds, f, {
            "source": "ERA5 (ARCO-ERA5, Google Cloud)", "licence": D["licence"],
            "attribution": "Contains modified Copernicus Climate Change Service information; "
                           "neither the European Commission nor ECMWF is responsible for any "
                           "use of it."}))
        print(f"era5 {year}-{m:02d}: {ds.sizes['time']} h, {f.stat().st_size / 2**20:.0f} MiB",
              flush=True)
    return files
