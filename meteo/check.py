"""Content checks of built NetCDF files (validation runs, before any bulk launch).

For each file: dimensions, inits present vs expected (days x runs), leads and members vs
config.yaml, NaN fractions, physical ranges, missing (all-NaN) lead slices, and a comparison with
ERA5 (ARCO) at the valid times for wind (u10/v10), waves (swh) and sea temperature (thetao vs SST).
Prints a table and writes a JSON report; exit code 1 when a hard check fails.

    python -m meteo.check out/*.nc --json check.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .common import CFG, expand_steps

RANGES = {"u10": (-75, 75), "v10": (-75, 75), "swh": (0, 25), "shww": (0, 25), "shts": (0, 25),
          "mwd": (0, 360), "mdww": (0, 360), "mdts": (0, 360), "dirpw": (0, 360),
          "mwp": (0, 30), "pp1d": (0, 40), "mpww": (0, 30), "mpts": (0, 40),
          "sst": (265, 310), "thetao": (-3, 35), "uo": (-4, 4), "vo": (-4, 4)}


def expectations(name: str) -> dict:
    """Expected leads / members / runs per init day, from config.yaml and the file name."""
    D = CFG["datasets"]
    m = re.match(r"ecmwf_(oper|aifs|enfo|wave)_", name)
    if m:
        c = D["ecmwf_open"]["products"][m.group(1)]
        return {"leads": expand_steps(c["steps"]), "runs": len(c["runs"]),
                "members": (c.get("members") or 0) + 1 if m.group(1) == "enfo" else None}
    for key in ("gefs_reforecast", "gefs_oper", "gefs_ctl"):
        if name.startswith(key):
            c = D[key]
            mem = {"gefs_oper": lambda: int(c["members"]) + 1, "gefs_ctl": lambda: 1}.get(
                key, lambda: None)()
            return {"leads": expand_steps(c["leads_h"]), "runs": 1, "members": mem}
    if name.startswith("gfs_wave"):
        c = D["gfs_wave"]
        return {"leads": expand_steps(c["steps"]), "runs": len(c["runs"]), "members": None}
    return {}


def _days_in(name: str) -> int | None:
    m = re.search(r"_(\d{8})\.nc$", name)
    if m:
        return 1
    m = re.search(r"_(\d{4})(\d{2})\.nc$", name)
    if m:
        return pd.Period(f"{m.group(1)}-{m.group(2)}").days_in_month
    return None


_ERA5 = None


def era5():
    global _ERA5
    if _ERA5 is None:
        from .era5 import open_arco
        _ERA5 = open_arco(["u10", "v10", "swh", "sst"]).rename(
            {"latitude": "lat", "longitude": "lon"})
    return _ERA5


def vs_era5(ds: xr.Dataset, var: str, ref_var: str, leads=(0, 24, 120), n_init=3,
            offset=0.0) -> dict:
    """RMSE / bias / correlation of the forecast (control or first member) against ERA5."""
    out = {}
    E = era5()[ref_var]
    lat = ds["lat"].values
    lon = ds["lon"].values % 360  # ARCO longitudes are 0..360
    inits = ds["init"].values if "init" in ds.dims else [None]
    pick = [inits[i] for i in np.linspace(0, len(inits) - 1, min(n_init, len(inits))).astype(int)]
    for ld in leads:
        if "lead" in ds.dims and ld not in ds["lead"].values:
            continue
        errs, fs, rs = [], [], []
        for t in pick:
            f = ds[var]
            if "init" in f.dims:
                f = f.sel(init=t)
            if "lead" in f.dims:
                f = f.sel(lead=ld)
            if "member" in f.dims:
                f = f.isel(member=0)
            if "time" in f.dims:  # reanalysis-like files (cmems): daily means
                f = f.isel(time=0)
                vt = pd.Timestamp(ds["time"].values[0])
            else:
                vt = pd.Timestamp(t) + pd.Timedelta(hours=int(ld))
            Et = E.sel(time=vt, method="nearest")
            if abs(pd.Timestamp(Et["time"].values) - vt) > pd.Timedelta(hours=3):
                continue  # valid time beyond the ERA5 archive
            r = Et.sel(lat=lat, lon=lon, method="nearest").values
            fv = f.values.astype("float64")
            r = r.astype("float64") + offset
            ok = np.isfinite(fv) & np.isfinite(r)
            if ok.sum() < 100:
                continue
            errs.append(fv[ok] - r[ok])
            fs.append(fv[ok])
            rs.append(r[ok])
        if errs:
            e = np.concatenate(errs)
            out[f"lead{ld}"] = {"rmse": round(float(np.sqrt(np.mean(e ** 2))), 3),
                                "bias": round(float(e.mean()), 3),
                                "corr": round(float(np.corrcoef(np.concatenate(fs),
                                                                np.concatenate(rs))[0, 1]), 3)}
    return out


def check_file(path: Path, compare: bool = True) -> dict:
    ds = xr.open_dataset(path)
    name = path.name
    rep = {"file": name, "MiB": round(path.stat().st_size / 2 ** 20, 1),
           "sizes": {k: int(v) for k, v in ds.sizes.items()}, "vars": {}, "fail": [], "warn": []}
    exp = expectations(name)
    if "init" in ds.dims:
        days = _days_in(name)
        if days and exp.get("runs"):
            want = days * exp["runs"]
            rep["inits"] = f"{ds.sizes['init']}/{want}"
            if ds.sizes["init"] < want:
                # runs absent at the source are known from meteo.probe: a warning, not a failure
                rep["warn"].append(f"inits {ds.sizes['init']}/{want}")
    if exp.get("leads") and "lead" in ds.dims:
        got = set(ds["lead"].values.tolist())
        miss = sorted(set(exp["leads"]) - got)
        rep["leads"] = f"{len(got)}/{len(exp['leads'])}"
        if miss:
            rep["fail"].append(f"leads missing {miss[:8]}{'...' if len(miss) > 8 else ''}")
    if exp.get("members") and "member" in ds.dims and ds.sizes["member"] != exp["members"]:
        rep["fail"].append(f"members {ds.sizes['member']}/{exp['members']}")
    for v in ds.data_vars:
        a = ds[v].values
        fin = np.isfinite(a)
        st = {"nan": round(float(1 - fin.mean()), 4)}
        if fin.any():
            st.update(min=round(float(a[fin].min()), 2), max=round(float(a[fin].max()), 2),
                      mean=round(float(a[fin].mean()), 3))
            lo, hi = RANGES.get(v, (-np.inf, np.inf))
            if st["min"] < lo - 1e-6 or st["max"] > hi + 1e-6:
                rep["fail"].append(f"{v} out of range [{st['min']}, {st['max']}]")
        else:
            rep["fail"].append(f"{v} all NaN")
        # whole (init, lead[, member]) slices that are entirely NaN = missing fields
        sp = [d for d in ("lat", "lon") if d in ds[v].dims]
        if sp and len(ds[v].dims) > 2:
            empty = (~fin).all(axis=tuple(ds[v].get_axis_num(sp)))
            st["empty_slices"] = f"{int(empty.sum())}/{empty.size}"
            if empty.mean() > 0.02 and not name.startswith("gefs_reforecast"):
                rep["warn"].append(f"{v} empty slices {int(empty.sum())}/{empty.size}")
        if v in ("u10", "v10") and not name.startswith("gefs_reforecast"):
            # NaN inside fields that exist (whole missing fields are reported as empty slices)
            if sp and len(ds[v].dims) > 2:
                part = (~fin).sum() - int(empty.sum()) * int(np.prod([ds.sizes[d] for d in sp]))
                st["nan_in_fields"] = round(float(part / max(fin.size, 1)), 4)
            else:
                st["nan_in_fields"] = st["nan"]
            if st["nan_in_fields"] > 0.01:
                rep["fail"].append(f"{v} NaN fraction inside fields {st['nan_in_fields']}")
        rep["vars"][v] = st
    if compare:
        try:
            if "u10" in ds:
                rep["era5_u10"] = vs_era5(ds, "u10", "u10")
                rep["era5_v10"] = vs_era5(ds, "v10", "v10")
                r0 = rep["era5_u10"].get("lead0")
                if r0 and (r0["rmse"] > 3.0 or r0["corr"] < 0.8):
                    rep["fail"].append(f"u10 lead0 vs ERA5 rmse {r0['rmse']} corr {r0['corr']}")
            if "swh" in ds:
                rep["era5_swh"] = vs_era5(ds, "swh", "swh")
                r0 = rep["era5_swh"].get("lead0")
                if r0 and (r0["rmse"] > 1.0 or r0["corr"] < 0.8):
                    rep["fail"].append(f"swh lead0 vs ERA5 rmse {r0['rmse']} corr {r0['corr']}")
            if "thetao" in ds:
                rep["era5_sst"] = vs_era5(ds, "thetao", "sst", leads=(0, 72), offset=-273.15)
        except Exception as exc:  # noqa: BLE001
            rep["warn"].append(f"ERA5 comparison failed: {exc!r}")
    rep["status"] = "FAIL" if rep["fail"] else ("WARN" if rep["warn"] else "OK")
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", default="check.json")
    ap.add_argument("--no-era5", action="store_true")
    ap.add_argument("--max-compare", type=int, default=3, help="ERA5 comparison on N files")
    a = ap.parse_args()
    files = sorted(Path(f) for f in a.files if f.endswith(".nc"))
    reps = []
    for i, f in enumerate(files):
        r = check_file(f, compare=not a.no_era5 and (i < a.max_compare or i == len(files) - 1))
        reps.append(r)
        e5 = {k: v for k, v in r.items() if k.startswith("era5_")}
        print(f"{r['status']:4s} {r['file']:44s} {r['MiB']:7.1f} MiB {r['sizes']} "
              f"inits {r.get('inits', '-')} leads {r.get('leads', '-')} "
              f"{'; '.join(r['fail'] + r['warn'])}", flush=True)
        for k, v in e5.items():
            print(f"      {k}: {v}", flush=True)
    Path(a.json).write_text(json.dumps(reps, indent=1))
    tot = sum(r["MiB"] for r in reps)
    print(f"{len(reps)} file(s), {tot:.0f} MiB, FAIL {sum(r['status'] == 'FAIL' for r in reps)}, "
          f"WARN {sum(r['status'] == 'WARN' for r in reps)}")
    sys.exit(1 if any(r["status"] == "FAIL" for r in reps) else 0)


if __name__ == "__main__":
    main()
