"""Shared helpers: study box, regridding to the storage grids, compact NetCDF writing, manifest.

Storage conventions (identical for every dataset, so that agents can open any file the same way):

* variables ``u10`` / ``v10`` [m/s], wind *towards* (meteorological u/v components);
* longitudes in [-180, 180), latitudes ascending, regular grid (0.5 deg or 1.0 deg);
* int16 with scale_factor 0.01 m/s, zlib level 4 + shuffle (lossless to 0.005 m/s);
* forecast files: dims (init, [member,] lead, lat, lon); ``lead`` in hours (int16);
* one file per year (reanalysis) or per month (forecasts), each < 2 GiB (GitHub Release limit).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import xarray as xr
import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
BOX = CFG["box"]  # lat_min, lat_max, lon_min, lon_max (lon in [-180, 180))
ENC = {"dtype": "int16", "scale_factor": 0.01, "_FillValue": -32768, "zlib": True,
       "complevel": 4, "shuffle": True}


def to_pm180(ds: xr.Dataset, lon: str = "longitude") -> xr.Dataset:
    """Longitudes 0..360 -> -180..180, sorted ascending."""
    ds = ds.assign_coords({lon: ((ds[lon] + 180) % 360) - 180})
    return ds.sortby(lon)


def standardise(ds: xr.Dataset) -> xr.Dataset:
    """Rename coordinates to lat/lon, longitudes to [-180, 180), latitudes ascending."""
    ren = {k: v for k, v in {"latitude": "lat", "longitude": "lon"}.items() if k in ds.dims}
    ds = ds.rename(ren)
    ds = ds.assign_coords(lon=((ds["lon"] + 180) % 360) - 180).sortby("lon").sortby("lat")
    return ds


def cut_box(ds: xr.Dataset, pad: float = 0.0) -> xr.Dataset:
    """Select the study box (after :func:`standardise`)."""
    return ds.sel(lat=slice(BOX["lat_min"] - pad, BOX["lat_max"] + pad),
                  lon=slice(BOX["lon_min"] - pad, BOX["lon_max"] + pad))


def target_grid(res: float) -> tuple[np.ndarray, np.ndarray]:
    lat = np.round(np.arange(BOX["lat_min"], BOX["lat_max"] + 1e-9, res), 4)
    lon = np.round(np.arange(BOX["lon_min"], BOX["lon_max"] + 1e-9, res), 4)
    return lat, lon


def regrid(ds: xr.Dataset, res: float) -> xr.Dataset:
    """Box-average to a regular ``res``-degree grid aligned on the box corners.

    Source grids that are finer than ``res`` are block-averaged (cell means of u and v, which is
    what a 40 km routing grid sees); equal resolutions are a plain selection; coarser sources are
    linearly interpolated (only used for the 1.5 deg S2S option).
    """
    lat, lon = target_grid(res)
    src = float(abs(ds["lat"].diff("lat").median()))
    if src < res - 1e-6:
        half = res / 2
        ds = cut_box(ds, pad=half)
        lat_b = np.round(np.concatenate([lat - half, [lat[-1] + half]]), 4)
        lon_b = np.round(np.concatenate([lon - half, [lon[-1] + half]]), 4)
        ds = ds.groupby_bins("lat", lat_b, labels=lat, include_lowest=True).mean("lat")
        ds = ds.rename(lat_bins="lat")
        ds = ds.groupby_bins("lon", lon_b, labels=lon, include_lowest=True).mean("lon")
        ds = ds.rename(lon_bins="lon")
        ds = ds.assign_coords(lat=lat.astype("float32"), lon=lon.astype("float32"))
        return ds.transpose(..., "lat", "lon")
    out = ds.interp(lat=lat, lon=lon) if src > res + 1e-6 else ds.sel(lat=lat, lon=lon,
                                                                       method="nearest")
    out = out.assign_coords(lat=lat.astype("float32"), lon=lon.astype("float32"))
    return out.transpose(..., "lat", "lon")


def write_nc(ds: xr.Dataset, path: Path, attrs: dict) -> Path:
    """Compact, self-describing NetCDF4 (see module docstring)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ds = ds[["u10", "v10"]]
    ds.attrs.update({"conventions": "u10/v10 towards, m/s; lon [-180,180); lead in hours",
                     "box": json.dumps(BOX), **attrs})
    ds["u10"].attrs.update(units="m s-1", long_name="10 m eastward wind")
    ds["v10"].attrs.update(units="m s-1", long_name="10 m northward wind")
    enc = {v: dict(ENC) for v in ("u10", "v10")}
    tmp = path.with_suffix(".tmp.nc")
    ds.to_netcdf(tmp, encoding=enc, engine="netcdf4")
    tmp.replace(path)
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_entry(path: Path, **meta) -> dict:
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path), **meta}
