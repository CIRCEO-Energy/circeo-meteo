"""ERA5 from Google's public ARCO-ERA5 Zarr (no account, no queue): wind, waves, SST.

* ``era5``       hourly 10 m wind, native 0.25 deg, one file per month (``era5_uv10_0p25_*``);
* ``era5_waves`` hourly wave parameters on the native 0.5 deg wave grid (points selected, no
  averaging: directions cannot be averaged arithmetically), one file per month;
* ``era5_sst``   daily-mean SST, 0.25 deg, one file per year.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import xarray as xr

from .common import CFG, cut_box, regrid, res_tag, standardise, write_nc

ARCO = {  # storage name -> ARCO-ERA5 variable name
    "u10": "10m_u_component_of_wind", "v10": "10m_v_component_of_wind",
    "swh": "significant_height_of_combined_wind_waves_and_swell",
    "mwd": "mean_wave_direction", "mwp": "mean_wave_period", "pp1d": "peak_wave_period",
    "shww": "significant_height_of_wind_waves", "mdww": "mean_direction_of_wind_waves",
    "mpww": "mean_period_of_wind_waves", "shts": "significant_height_of_total_swell",
    "mdts": "mean_direction_of_total_swell", "mpts": "mean_period_of_total_swell",
    "sst": "sea_surface_temperature",
}
PREFIX = {"era5": "era5_uv10", "era5_waves": "era5_waves", "era5_sst": "era5_sst"}
ATTR = ("Contains modified Copernicus Climate Change Service information; neither the European "
        "Commission nor ECMWF is responsible for any use of it.")


def open_arco(names: list[str]) -> xr.Dataset:
    ds = xr.open_zarr(CFG["datasets"]["era5"]["source"], chunks=None,
                      storage_options={"token": "anon"})
    have = {k: v for k, v in ARCO.items() if k in names and v in ds}
    missing = sorted(set(names) - set(have))
    if missing:
        print(f"ARCO-ERA5: not available, skipped: {missing}", flush=True)
    return ds[list(have.values())].rename({v: k for k, v in have.items()})


def _select(ds: xr.Dataset, res: float) -> xr.Dataset:
    """Pick the grid points of a coarser regular grid (no averaging)."""
    from .common import target_grid
    lat, lon = target_grid(res)
    out = ds.sel(lat=lat, lon=lon, method="nearest")
    return out.assign_coords(lat=lat.astype("float32"), lon=lon.astype("float32"))


def build_year(year: int, out: Path, months: list[int] | None = None,
               dataset: str = "era5") -> list[Path]:
    D = CFG["datasets"][dataset]
    src = open_arco(D["vars"])
    files = []
    if dataset == "era5_sst":
        parts = []
        for m in months or range(1, 13):
            t0 = pd.Timestamp(year, m, 1)
            sub = src.sel(time=slice(t0, t0 + pd.offsets.MonthBegin(1) - pd.Timedelta(hours=1)))
            sub = cut_box(standardise(sub), pad=0.5).load().resample(time="1D").mean()
            parts.append(regrid(sub, D["res"]).astype("float32"))
            print(f"{dataset} {year}-{m:02d} ok", flush=True)
        f = out / f"{PREFIX[dataset]}_{res_tag(D['res'])}_{year}.nc"
        return [write_nc(xr.concat(parts, "time"), f, {"source": "ERA5 (ARCO-ERA5)",
                                                        "licence": D["licence"],
                                                        "attribution": ATTR})]
    for m in months or range(1, 13):
        t0 = pd.Timestamp(year, m, 1)
        sub = src.sel(time=slice(t0, t0 + pd.offsets.MonthBegin(1) - pd.Timedelta(hours=1)))
        sub = cut_box(standardise(sub), pad=0.5).load()
        ds = (_select(sub, D["res"]) if dataset == "era5_waves" else regrid(sub, D["res"]))
        f = out / f"{PREFIX[dataset]}_{res_tag(D['res'])}_{year}{m:02d}.nc"
        files.append(write_nc(ds.astype("float32"), f, {"source": "ERA5 (ARCO-ERA5)",
                                                        "licence": D["licence"],
                                                        "attribution": ATTR}))
        print(f"{dataset} {year}-{m:02d}: {ds.sizes['time']} h, "
              f"{f.stat().st_size / 2**20:.0f} MiB", flush=True)
    return files
