"""Shared helpers: study box, regridding to the storage grids, compact NetCDF writing, manifest.

Storage conventions (identical for every dataset, so that agents can open any file the same way):

* variables ``u10`` / ``v10`` [m/s], wind *towards* (meteorological u/v components);
* longitudes in [-180, 180), latitudes ascending, regular grid (0.25, 0.5 or 1.0 deg);
* int16 with scale_factor 0.01 m/s, zlib level 4 + shuffle (lossless to 0.005 m/s);
* forecast files: dims (init, [member,] lead, lat, lon); ``lead`` in hours (int16);
* one file per month, each < 2 GiB (GitHub Release limit).
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


def res_tag(res: float) -> str:
    """0.25 -> '0p25', 1.0 -> '1p0'."""
    t = f"{res:.2f}".rstrip("0")
    t = t + "0" if t.endswith(".") else t
    return t.replace(".", "p")


def expand_steps(spec: list[list[int]]) -> list[int]:
    """[[0, 144, 3], [150, 240, 6]] -> [0, 3, ..., 144, 150, ..., 240]."""
    out = []
    for a, b, d in spec:
        out += [x for x in range(a, b + 1, d) if x not in out]
    return out


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


DIRECTIONS = {"mwd", "mdww", "mdts", "dirpw"}


def regrid(ds: xr.Dataset, res: float) -> xr.Dataset:
    """Box-average to a regular ``res``-degree grid aligned on the box corners.

    Source grids that are finer than ``res`` are block-averaged (cell means of u and v, which is
    what a 40 km routing grid sees); equal resolutions are a plain selection; coarser sources are
    linearly interpolated (only used for the 1.5 deg S2S option).
    """
    lat, lon = target_grid(res)
    src = float(abs(ds["lat"].diff("lat").median()))
    if src < res - 1e-6 and set(ds.data_vars) & DIRECTIONS:
        # directions cannot be averaged arithmetically: pick the coarse grid points instead
        out = ds.sel(lat=lat, lon=lon, method="nearest")
        out = out.assign_coords(lat=lat.astype("float32"), lon=lon.astype("float32"))
        return out.transpose(..., "lat", "lon")
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


# Storage variables: name -> (units, long name, scale_factor, add_offset). int16 packing:
# the representable range is add_offset +/- 327.67 * (scale_factor / 0.01).
VARS = {
    "u10": ("m s-1", "10 m eastward wind (towards)", 0.01, 0.0),
    "v10": ("m s-1", "10 m northward wind (towards)", 0.01, 0.0),
    "swh": ("m", "significant height of combined wind waves and swell", 0.001, 16.0),
    "mwd": ("degree", "mean wave direction (coming from, clockwise from north)", 0.01, 180.0),
    "mwp": ("s", "mean wave period", 0.001, 16.0),
    "pp1d": ("s", "peak wave period", 0.001, 16.0),
    "shww": ("m", "significant height of wind waves", 0.001, 16.0),
    "mdww": ("degree", "mean direction of wind waves (coming from)", 0.01, 180.0),
    "mpww": ("s", "mean period of wind waves", 0.001, 16.0),
    "shts": ("m", "significant height of total swell", 0.001, 16.0),
    "mdts": ("degree", "mean direction of total swell (coming from)", 0.01, 180.0),
    "mpts": ("s", "mean period of total swell", 0.001, 16.0),
    "dirpw": ("degree", "peak wave direction (coming from)", 0.01, 180.0),
    "sst": ("K", "sea surface temperature", 0.001, 290.0),
    "uo": ("m s-1", "eastward sea water velocity (towards)", 0.0001, 0.0),
    "vo": ("m s-1", "northward sea water velocity (towards)", 0.0001, 0.0),
    "thetao": ("degC", "sea water potential temperature (surface layer)", 0.001, 12.0),
}


def write_nc(ds: xr.Dataset, path: Path, attrs: dict) -> Path:
    """Compact, self-describing NetCDF4: every known variable of ``ds`` packed as int16."""
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = [v for v in VARS if v in ds.data_vars]
    ds = ds[keep]
    ds.attrs.update({"conventions": "lon [-180,180); lead in hours; winds and currents "
                                    "towards, wave directions coming from",
                     "box": json.dumps(BOX), **attrs})
    enc = {}
    for v in keep:
        units, name, scale, offset = VARS[v]
        ds[v].attrs.update(units=units, long_name=name)
        enc[v] = {**ENC, "scale_factor": scale, "add_offset": offset}
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


def release_for(path: Path) -> str:
    """Release tag of an output file, from config.yaml (``{year}`` filled from the file name)."""
    import re
    name = Path(path).name
    if name.startswith("manifest_"):
        name = name[len("manifest_"):].rsplit(".", 1)[0] + ".nc"
    stems = {}
    for key, d in CFG["datasets"].items():
        if key == "ecmwf_open":
            for p, c in d["products"].items():
                fam = "waves" if p == "wave" else "uv10"
                stems[f"ecmwf_{p}_{fam}_{res_tag(c['res'])}_"] = c["release"]
        elif "release" in d:
            stems[key + "_"] = d["release"]
            if key.startswith("gefs_"):
                stems[f"{key}_uv10_{res_tag(d['res'])}_"] = d["release"]
    stems.update({"era5_uv10_": CFG["datasets"]["era5"]["release"],
                  "era5_waves_": CFG["datasets"]["era5_waves"]["release"],
                  "era5_sst_": CFG["datasets"]["era5_sst"]["release"],
                  "gfs_wave_": CFG["datasets"]["gfs_wave"]["release"]})
    stem = max((k for k in stems if name.startswith(k)), key=len, default=None)
    if stem is None:
        raise KeyError(f"no release for {name}")
    year = re.search(r"_(\d{4})", name[len(stem) - 1:])
    return stems[stem].format(year=year.group(1) if year else "")
