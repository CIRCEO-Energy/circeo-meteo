"""Forecast-vs-ERA5 scores computed from the ARCHIVED forecast files (no new download from the
sources): every product is scored on its own archive grid against ERA5 brought to that grid with
the same block-mean regridding as the forecasts.

Two outputs per product and month, both small (``scores_<stem>_<YYYYMM>.nc``):

* time series, dims (init, lead), means over the ocean points of the box:
  ``rmse_vec`` (vector wind error, control/deterministic), ``bias_spd``, ``mae_spd``;
  for ensembles also ``em_rmse_vec`` (ensemble mean), ``spread`` (sqrt of the mean ensemble
  variance, u+v), ``crps_spd`` (fair CRPS of the wind speed). Waves: the same on ``swh``.
* maps, dims (map_lead, lat, lon), SUMS over the month's inits (divide by ``n``):
  ``n``, ``se_vec``, ``d_spd``, ``ad_spd`` (+ ``em_se_vec``, ``var``, ``crps_spd`` for ensembles).

    python -m meteo.scores --files fc/*.nc --era5 era5/ --mask era5_sst_0p25_2023.nc --out scores/
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .common import regrid

MAP_LEADS = (0, 24, 48, 72, 120, 168, 240, 360)


def fair_crps(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Fair CRPS of ensemble ``x`` (member axis 0) against ``y``."""
    m = x.shape[0]
    t1 = np.abs(x - y[None]).mean(0)
    xs = np.sort(x, axis=0)
    w = (2 * np.arange(1, m + 1) - m - 1).reshape((m,) + (1,) * (x.ndim - 1))
    t2 = 2 * (w * xs).sum(0) / (m * (m - 1))  # = sum_ij |xi-xj| / (m(m-1))
    return t1 - 0.5 * t2


class Truth:
    """ERA5 fields at a forecast grid, loaded month by month on demand."""

    def __init__(self, era5_dir: Path, kind: str, lat, lon):
        self.dir, self.kind, self.lat, self.lon = Path(era5_dir), kind, lat, lon
        self.cache: dict = {}
        self.res = float(np.round(lat[1] - lat[0], 4))

    def _month(self, ym: str):
        if ym not in self.cache:
            pat = (f"era5_uv10_0p25_{ym}.nc" if self.kind == "wind" else f"era5_waves_0p5_{ym}.nc")
            f = next(self.dir.rglob(pat), None)
            if f is None:
                self.cache[ym] = None
            else:
                ds = xr.open_dataset(f)
                ds = ds[["u10", "v10"]] if self.kind == "wind" else ds[["swh"]]
                ds = ds.load()
                src_res = float(np.round(ds.lat[1] - ds.lat[0], 4))
                if self.res > src_res + 1e-6:
                    ds = regrid(ds, self.res)
                self.cache[ym] = ds.sel(lat=self.lat, lon=self.lon, method="nearest")
            if len(self.cache) > 3:
                self.cache.pop(next(iter(self.cache)))
        return self.cache[ym]

    def at(self, t: pd.Timestamp):
        ds = self._month(f"{t:%Y%m}")
        if ds is None or t not in ds.indexes["time"]:
            return None
        return ds.sel(time=t)


ROUTER_POINTS = Path(__file__).with_name("data_router_points.npz")
"""Sea cells of the HORIZON planning grid (LAEA 40 km, 32W-5E, 38-65N): the router's view."""


def bilinear(lat_src, lon_src, plat, plon):
    """Weights to interpolate a regular (lat, lon) field at points (plat, plon)."""
    fj = (plat - lat_src[0]) / (lat_src[1] - lat_src[0])
    fi = (plon - lon_src[0]) / (lon_src[1] - lon_src[0])
    j0 = np.clip(np.floor(fj).astype(int), 0, lat_src.size - 2)
    i0 = np.clip(np.floor(fi).astype(int), 0, lon_src.size - 2)
    wj, wi = fj - j0, fi - i0
    return j0, i0, wj, wi


def at_points(a: np.ndarray, w) -> np.ndarray:
    """``a`` (..., lat, lon) -> (..., P)."""
    j0, i0, wj, wi = w
    return (a[..., j0, i0] * (1 - wj) * (1 - wi) + a[..., j0, i0 + 1] * (1 - wj) * wi
            + a[..., j0 + 1, i0] * wj * (1 - wi) + a[..., j0 + 1, i0 + 1] * wj * wi)


def router_metrics(fu, fv, tu, tv) -> dict:
    """Router-view metrics at points: vector error and the MSE decomposition of wind speed."""
    ok = np.isfinite(fu) & np.isfinite(tu)
    if ok.mean() < 0.95:
        return {}
    fu, fv, tu, tv = fu[ok], fv[ok], tu[ok], tv[ok]
    sf, so = np.hypot(fu, fv), np.hypot(tu, tv)
    d = sf - so
    sdf, sdo = sf.std(), so.std()
    r = np.corrcoef(sf, so)[0, 1]
    return {"r_se_vec": float(((fu - tu) ** 2 + (fv - tv) ** 2).mean()),
            "r_bias_spd": float(d.mean()), "r_mse_spd": float((d ** 2).mean()),
            "r_t_bias": float(d.mean() ** 2), "r_t_amp": float((sdf - sdo) ** 2),
            "r_t_phase": float(2 * sdf * sdo * (1 - r))}


class TruthPoints:
    """ERA5 at its native 0.25 deg, bilinear at the router points (as the router reads ERA5)."""

    def __init__(self, era5_dir: Path, pts):
        self.dir, self.pts, self.cache, self.w = Path(era5_dir), pts, {}, None

    def at(self, t: pd.Timestamp):
        ym = f"{t:%Y%m}"
        if ym not in self.cache:
            f = next(self.dir.rglob(f"era5_uv10_0p25_{ym}.nc"), None)
            if f is None:
                self.cache[ym] = None
            else:
                ds = xr.open_dataset(f)
                if self.w is None:
                    self.w = bilinear(ds.lat.values, ds.lon.values, *self.pts)
                self.cache[ym] = (ds.indexes["time"], at_points(ds.u10.values, self.w),
                                  at_points(ds.v10.values, self.w))
            if len(self.cache) > 3:
                self.cache.pop(next(iter(self.cache)))
        c = self.cache[ym]
        if c is None or t not in c[0]:
            return None
        k = c[0].get_loc(t)
        return c[1][k], c[2][k]


def ocean_mask(sst_file: Path, lat, lon) -> np.ndarray:
    s = xr.open_dataset(sst_file)["sst"].isel(time=0).load()
    wet = np.isfinite(s).astype("float32")
    res = float(np.round(lat[1] - lat[0], 4))
    if res > 0.25 + 1e-6:
        wet = regrid(wet.to_dataset(name="w"), res)["w"]
    return (wet.sel(lat=lat, lon=lon, method="nearest").values >= 0.5)


def score_files(files: list[Path], era5_dir: Path, sst: Path) -> xr.Dataset:
    first = xr.open_dataset(files[0])
    kind = "wind" if "u10" in first else "waves"
    lat, lon = first["lat"].values, first["lon"].values
    truth = Truth(era5_dir, kind, lat, lon)
    wet = ocean_mask(sst, lat, lon)
    ens = "member" in first.dims and first.sizes["member"] > 1
    leads = first["lead"].values
    mleads = [ld for ld in MAP_LEADS if ld in leads]
    ts_vars = ["rmse_vec", "bias_spd", "mae_spd"] + (["em_rmse_vec", "spread", "crps_spd"] if ens else [])
    mp_vars = ["n", "se_vec", "d_spd", "ad_spd"] + (["em_se_vec", "var", "crps_spd"] if ens else [])
    mp = {v: np.zeros((len(mleads), lat.size, lon.size), "float64") for v in mp_vars}
    rv = ["r_se_vec", "r_bias_spd", "r_mse_spd", "r_t_bias", "r_t_amp", "r_t_phase"] + (
        ["r_em_se_vec", "r_var"] if ens else [])
    rpts = None
    if kind == "wind" and ROUTER_POINTS.exists():
        z = np.load(ROUTER_POINTS)
        pts = (z["lat"].astype("float64"), z["lon"].astype("float64"))
        rpts = (TruthPoints(era5_dir, pts), bilinear(lat, lon, *pts))
        ts_vars = ts_vars + rv
    rows, inits = [], []
    for f in files:
        ds = xr.open_dataset(f)
        for it in ds["init"].values:
            t0 = pd.Timestamp(it)
            ts = {v: np.full(len(leads), np.nan, "float32") for v in ts_vars}
            fi = ds.sel(init=it).load()
            for k, ld in enumerate(leads):
                tr = truth.at(t0 + pd.Timedelta(hours=int(ld)))
                if tr is None:
                    continue
                fl = fi.sel(lead=ld)
                if kind == "wind":
                    U, V = fl["u10"].values, fl["v10"].values
                    tu, tv = tr["u10"].values, tr["v10"].values
                    if U.ndim == 3:  # member, lat, lon
                        cu, cv = U[0], V[0]
                    else:
                        cu, cv = U, V
                    ok = wet & np.isfinite(cu) & np.isfinite(tu)
                    if ok.sum() < 50:
                        continue
                    se = (cu - tu) ** 2 + (cv - tv) ** 2
                    ts_spd = np.hypot(tu, tv)
                    dsp = np.hypot(cu, cv) - ts_spd
                else:
                    S = fl["swh"].values
                    cs = S[0] if S.ndim == 3 else S
                    t_s = tr["swh"].values
                    ok = wet & np.isfinite(cs) & np.isfinite(t_s)
                    if ok.sum() < 50:
                        continue
                    se = (cs - t_s) ** 2
                    dsp = cs - t_s
                ts["rmse_vec"][k] = np.sqrt(se[ok].mean())
                ts["bias_spd"][k] = dsp[ok].mean()
                ts["mae_spd"][k] = np.abs(dsp[ok]).mean()
                if rpts is not None:
                    tp = rpts[0].at(t0 + pd.Timedelta(hours=int(ld)))
                    if tp is not None:
                        w = rpts[1]
                        rm = router_metrics(at_points(cu, w), at_points(cv, w), *tp)
                        if rm and ens:
                            good = np.isfinite(U).all(axis=(1, 2))
                            if good.sum() > 2:
                                Ug, Vg = U[good], V[good]
                                eu, ev = at_points(Ug.mean(0), w), at_points(Vg.mean(0), w)
                                rm["r_em_se_vec"] = float(np.nanmean((eu - tp[0]) ** 2 + (ev - tp[1]) ** 2))
                                rm["r_var"] = float(np.nanmean(at_points(
                                    Ug.var(0, ddof=1) + Vg.var(0, ddof=1), w)))
                        for kk, vv in rm.items():
                            ts[kk][k] = vv
                if ens and kind == "wind":
                    good = np.isfinite(U).all(axis=(1, 2))
                    Ue, Ve = U[good], V[good]
                    if Ue.shape[0] < 2:
                        continue
                    emse = (Ue.mean(0) - tu) ** 2 + (Ve.mean(0) - tv) ** 2
                    var = Ue.var(0, ddof=1) + Ve.var(0, ddof=1)
                    crps = fair_crps(np.hypot(Ue, Ve), ts_spd)
                    ts["em_rmse_vec"][k] = np.sqrt(emse[ok].mean())
                    ts["spread"][k] = np.sqrt(var[ok].mean())
                    ts["crps_spd"][k] = crps[ok].mean()
                if ld in mleads:
                    j = mleads.index(ld)
                    okf = ok.astype("float64")
                    mp["n"][j] += okf
                    mp["se_vec"][j] += np.where(ok, se, 0)
                    mp["d_spd"][j] += np.where(ok, dsp, 0)
                    mp["ad_spd"][j] += np.where(ok, np.abs(dsp), 0)
                    if ens and kind == "wind":
                        mp["em_se_vec"][j] += np.where(ok, emse, 0)
                        mp["var"][j] += np.where(ok, var, 0)
                        mp["crps_spd"][j] += np.where(ok, crps, 0)
            rows.append(ts)
            inits.append(np.datetime64(t0, "ns"))
            print(f"  {t0:%Y-%m-%d %H}z lead0 rmse {ts['rmse_vec'][0]:.2f} "
                  f"lead~120 {ts['rmse_vec'][min(np.searchsorted(leads, 120), len(leads) - 1)]:.2f}",
                  flush=True)
    out = xr.Dataset(
        {v: (("init", "lead"), np.stack([r[v] for r in rows])) for v in ts_vars},
        coords={"init": inits, "lead": leads.astype("int16")})
    for v in mp_vars:
        out[f"map_{v}"] = (("map_lead", "lat", "lon"), mp[v].astype("float32"))
    out = out.assign_coords(map_lead=np.array(mleads, "int16"), lat=lat, lon=lon)
    out.attrs.update(kind=kind, ensemble=int(ens), truth="ERA5 (ARCO, regridded like the forecasts)",
                     note="maps are sums over inits: divide by map_n")
    return out.sortby("init")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--era5", required=True, help="directory with era5_uv10 / era5_waves files")
    ap.add_argument("--mask", required=True, help="an era5_sst file (land = NaN)")
    ap.add_argument("--out", default="scores")
    ap.add_argument("--name", help="output name; default from the first file")
    a = ap.parse_args()
    files = sorted(Path(f) for f in a.files)
    res = score_files(files, Path(a.era5), Path(a.mask))
    stem = a.name or re.sub(r"_(\d{6})(\d{2})?(_d\d+)?\.nc$", r"_\1", files[0].name).replace(".nc", "")
    Path(a.out).mkdir(parents=True, exist_ok=True)
    p = Path(a.out) / f"scores_{stem}.nc"
    enc = {v: {"zlib": True, "complevel": 4} for v in res.data_vars}
    res.to_netcdf(p, encoding=enc)
    m = res["rmse_vec"].mean("init")
    print(p, f"{p.stat().st_size / 2**20:.1f} MiB", "rmse by lead:",
          {int(ld): round(float(m.sel(lead=ld)), 2) for ld in res["lead"].values[::8]})


if __name__ == "__main__":
    main()
