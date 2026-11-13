"""ERA5 hourly 10 m wind from Google's public ARCO-ERA5 Zarr (no account, no queue).

One output file per year: ``era5_uv10_0p5_<YYYY>.nc`` (time, lat, lon), hourly, 0.5 deg box means.
Read month by month to bound memory (~150 MB per variable per month at 0.25 deg).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import xarray as xr

from .common import CFG, cut_box, regrid, standardise, write_nc

D = CFG["datasets"]["era5"]
VARS = {"10m_u_component_of_wind": "u10", "10m_v_component_of_wind": "v10"}


def open_arco() -> xr.Dataset:
    ds = xr.open_zarr(D["source"], chunks=None, storage_options={"token": "anon"})
    return ds[list(VARS)].rename(VARS)


def build_year(year: int, out: Path, months: list[int] | None = None) -> list[Path]:
    src = open_arco()
    parts = []
    for m in months or range(1, 13):
        t0 = pd.Timestamp(year, m, 1)
        t1 = t0 + pd.offsets.MonthBegin(1) - pd.Timedelta(hours=1)
        sub = src.sel(time=slice(t0, t1))
        sub = standardise(sub)
        sub = cut_box(sub, pad=0.5).load()
        parts.append(regrid(sub, D["res"]).astype("float32"))
        print(f"era5 {year}-{m:02d}: {sub.sizes['time']} h", flush=True)
    ds = xr.concat(parts, "time")
    tag = f"{year}" if months is None else f"{year}_m{'-'.join(map(str, months))}"
    f = out / f"era5_uv10_0p5_{tag}.nc"
    return [write_nc(ds, f, {"source": "ERA5 (ARCO-ERA5, Google Cloud)",
                             "licence": D["licence"],
                             "attribution": "Contains modified Copernicus Climate Change Service "
                                            "information; neither the European Commission nor "
                                            "ECMWF is responsible for any use of it."})]
