"""Full-resolution verification of the wind ensembles on the runners (option 1 of S6 storage).

The ensembles are archived at 0.5 deg; their statistics are computed here from the native 0.25 deg
fields (read again from AWS) against ERA5 at 0.25 deg (ARCO-ERA5), and only the statistics are
stored. One file per product and month: ``verify_<product>_0p25_<YYYYMM>.nc`` with, per
(lead, lat, lon), sums over the month's runs (divide by ``n`` for means):

* ``n``          number of verified runs
* ``em_du/em_dv``      ensemble-mean error of u10 / v10           [m/s]
* ``em_se``      squared vector error of the ensemble mean           [m2/s2]
* ``ctl_se``     squared vector error of the control member          [m2/s2]
* ``var``        ensemble variance (u + v, unbiased)                 [m2/s2]
* ``em_dspd``    error of the ensemble-mean wind speed               [m/s]
* ``spd_crps``   fair CRPS of the wind-speed ensemble                [m/s]

Spread-skill: sqrt(var/n) vs sqrt(em_se/n); bias: em_du/n ...
Usage: ``python -m meteo.verify --product ecmwf_enfo|gefs_oper --year Y --month M [--days ..]``
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .common import CFG, cut_box, expand_steps, regrid, standardise

STATS = ("n", "em_du", "em_dv", "em_se", "ctl_se", "var", "em_dspd", "spd_crps")


def _truth(t0, t1) -> xr.Dataset:
    from .era5 import open_arco
    src = open_arco(["u10", "v10"]).sel(time=slice(t0, t1))
    return regrid(cut_box(standardise(src), pad=0.5).load(), 0.25)


def _fair_crps(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Fair CRPS over member axis 0: mean|X-y| - sum_{i,j}|Xi-Xj| / (2 M (M-1))."""
    m = x.shape[0]
    a = np.abs(x - y[None]).mean(0)
    xs = np.sort(x, axis=0)
    w = (2 * np.arange(1, m + 1) - m - 1).reshape((m,) + (1,) * (x.ndim - 1))
    b = (w * xs).sum(0) * 2 / (2 * m * (m - 1))
    return a - b


def _run(product: str, day: pd.Timestamp, tmp: Path) -> xr.Dataset:
    if product == "ecmwf_enfo":
        from .ecmwf_fetch import fetch_run
        c = CFG["datasets"]["ecmwf_open"]["products"]["enfo"]
        ds = fetch_run("enfo", day, 0, expand_steps(c["steps"]), 50)
        return regrid(ds, 0.25)
    D = CFG["datasets"]["gefs_oper"]
    mems = ["c00"] + [f"p{i:02d}" for i in range(1, D["members"] + 1)]
    return _gefs_full(day, tmp, mems)


def _gefs_full(day, tmp, mems):
    """All GEFS members at native 0.25 deg (pgrb2sp25), 0-240 h every 3 h."""
    import concurrent.futures as cf

    from .gefs import S3O, _download, _drop, _idx_ranges, _open10
    wanted = (":UGRD:10 m above ground:", ":VGRD:10 m above ground:")

    def one(job):
        mem, step = job
        url = (f"{S3O}/gefs.{day:%Y%m%d}/00/atmos/pgrb2sp25/"
               f"ge{mem}.t00z.pgrb2s.0p25.f{step:03d}")
        f = tmp / f"{mem}_{step:03d}.grib2"
        _download(url, f, _idx_ranges(url, wanted))
        ds = _drop(_open10(f)[["u10", "v10"]])
        f.unlink()
        return mem, step, ds
    with cf.ThreadPoolExecutor(16) as ex:
        res = list(ex.map(one, [(m, s) for m in mems for s in range(0, 241, 3)]))
    by = {}
    for mem, step, ds in res:
        by.setdefault(mem, []).append(ds.expand_dims(lead=[np.int16(step)]))
    ds = xr.concat([xr.concat(v, "lead").sortby("lead").expand_dims(member=[i])
                    for i, (m, v) in enumerate(sorted(by.items()))], "member")
    return regrid(ds, 0.25)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", choices=["ecmwf_enfo", "gefs_oper"], required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--month", type=int, required=True)
    ap.add_argument("--days", type=int, nargs="*")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    first = pd.Timestamp(a.year, a.month, 1)
    days = [d for d in pd.date_range(first, first + pd.offsets.MonthEnd(0), freq="D")
            if not a.days or d.day in a.days]
    truth = _truth(days[0], days[-1] + pd.Timedelta(days=16))
    acc = None
    with tempfile.TemporaryDirectory() as td:
        for day in days:
            try:
                fc = _run(a.product, day, Path(td))
            except Exception as exc:  # noqa: BLE001
                print(f"{a.product} {day:%Y-%m-%d} MISSING: {exc!r}", flush=True)
                continue
            valid = np.datetime64(day, "ns") + fc["lead"].values.astype("timedelta64[h]")
            ok = np.isin(valid, truth["time"].values)
            fc = fc.isel(lead=np.where(ok)[0])
            tr = truth.sel(time=valid[ok])
            u, v = fc["u10"].values, fc["v10"].values          # member, lead, lat, lon
            tu, tv = tr["u10"].values, tr["v10"].values         # lead, lat, lon
            mu, mv = u.mean(0), v.mean(0)
            spd, tspd = np.hypot(u, v), np.hypot(tu, tv)
            st = {"n": np.ones_like(tu), "em_du": mu - tu, "em_dv": mv - tv,
                  "em_se": (mu - tu) ** 2 + (mv - tv) ** 2,
                  "ctl_se": (u[0] - tu) ** 2 + (v[0] - tv) ** 2,
                  "var": u.var(0, ddof=1) + v.var(0, ddof=1),
                  "em_dspd": np.hypot(mu, mv) - tspd, "spd_crps": _fair_crps(spd, tspd)}
            leads = fc["lead"].values
            if acc is None:
                acc = {"lead": leads, "lat": fc["lat"].values, "lon": fc["lon"].values,
                       **{k: np.zeros_like(st["n"], dtype="float64") for k in STATS}}
            idx = np.searchsorted(acc["lead"], leads)
            for k in STATS:
                np.add.at(acc[k], idx, st[k])
            print(f"{a.product} {day:%Y-%m-%d} verified ({len(leads)} leads)", flush=True)
    if acc is None:
        raise SystemExit("nothing verified")
    ds = xr.Dataset({k: (("lead", "lat", "lon"), acc[k].astype("float32")) for k in STATS},
                    coords={"lead": acc["lead"], "lat": acc["lat"], "lon": acc["lon"]})
    ds.attrs.update(product=a.product, month=f"{a.year}-{a.month:02d}",
                    truth="ERA5 (ARCO-ERA5) 0.25 deg", note=__doc__.split("Usage")[0])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"verify_{a.product}_0p25_{a.year}{a.month:02d}.nc"
    ds.to_netcdf(f, encoding={k: {"zlib": True, "complevel": 4} for k in STATS})
    print(f"wrote {f} ({f.stat().st_size / 2**20:.0f} MiB)")


if __name__ == "__main__":
    main()
