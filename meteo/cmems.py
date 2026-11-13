"""Copernicus Marine Service (free account): surface currents, SST and waves.

* ``cmems_phy_reanalysis``: GLORYS12 (1/12 deg, daily means, 1993 -> about 2025), surface layer
  ``uo``/``vo`` (currents, towards) and ``thetao`` (temperature, ~SST); the recent months come
  from the "interim" dataset of the same product.
* ``cmems_phy_forecast``: global analysis & forecast, hourly surface fields. Copernicus Marine
  keeps the best analysis (not the past forecasts): the daily workflow therefore archives the
  current 10-day forecast every day (see ``archive_forecast``).
* ``cmems_wav_reanalysis``: WAVERYS (0.2 deg, 3-hourly), better than ERA5 waves according to
  Mercator Ocean (current forcing, wave-spectra assimilation).

Credentials: GitHub secrets COPERNICUSMARINE_SERVICE_USERNAME / COPERNICUSMARINE_SERVICE_PASSWORD
(read by the ``copernicusmarine`` toolbox from the environment). Native resolution is kept.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .common import BOX, CFG, write_nc

ATTR = "E.U. Copernicus Marine Service Information"


def _subset(dataset_id: str, variables: list[str], t0, t1, depth=None) -> xr.Dataset:
    import copernicusmarine as cm
    kw = dict(dataset_id=dataset_id, variables=variables,
              minimum_longitude=BOX["lon_min"], maximum_longitude=BOX["lon_max"],
              minimum_latitude=BOX["lat_min"], maximum_latitude=BOX["lat_max"],
              start_datetime=str(t0), end_datetime=str(t1))
    if depth is not None:
        kw.update(minimum_depth=depth[0], maximum_depth=depth[1])
    ds = cm.open_dataset(**kw)
    if "depth" in ds.dims:
        ds = ds.isel(depth=0, drop=True)
    return ds.rename({k: v for k, v in {"latitude": "lat", "longitude": "lon"}.items()
                      if k in ds.dims})


def build_month(dataset: str, year: int, month: int, out: Path) -> list[Path]:
    D = CFG["datasets"][dataset]
    t0 = pd.Timestamp(year, month, 1)
    t1 = t0 + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23)
    ids = [D["dataset_id"]] + ([D["interim_id"]] if D.get("interim_id") else [])
    last = None
    for did in ids:
        try:
            ds = _subset(did, list(D["vars"]), t0, t1, D.get("depth")).load()
            if ds.sizes.get("time", 0):
                break
        except Exception as exc:  # noqa: BLE001 - try the interim dataset
            last = exc
            print(f"{did}: {exc!r}", flush=True)
    else:
        raise RuntimeError(f"no data for {year}-{month:02d}: {last!r}")
    ds = ds.rename(D["vars"]).astype("float32")
    name = f"{dataset}_{year}{month:02d}.nc"
    print(f"{dataset} {year}-{month:02d}: {dict(ds.sizes)} from {did}", flush=True)
    return [write_nc(ds, out / name, {"source": did, "licence": D["licence"],
                                      "attribution": ATTR})]


def archive_forecast(out: Path, day: pd.Timestamp | None = None) -> list[Path]:
    """Today's analysis + 10-day forecast of the hourly surface product, as one 'init' file."""
    D = CFG["datasets"]["cmems_phy_forecast"]
    day = (day or pd.Timestamp.utcnow().tz_localize(None)).normalize()
    ds = _subset(D["dataset_id"], list(D["vars"]), day, day + pd.Timedelta(days=10)).load()
    ds = ds.rename(D["vars"]).astype("float32")
    lead = ((ds["time"].values - np.datetime64(day, "ns")) / np.timedelta64(1, "h"))
    ds = ds.assign_coords(lead=("time", lead.astype("int16"))).swap_dims(time="lead")
    ds = ds.drop_vars("time").expand_dims(init=[np.datetime64(day, "ns")])
    return [write_nc(ds, out / f"cmems_phy_forecast_{day:%Y%m%d}.nc",
                     {"source": D["dataset_id"], "licence": D["licence"], "attribution": ATTR})]
