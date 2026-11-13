"""Daily aggregation of the prospective archive into monthly files.

Every day the ``daily`` workflow:

1. downloads the current monthly files of each product from the dataset Release (if they exist);
2. lists which initialisations of the last ``--lookback`` days are missing from them;
3. fetches only those (ECMWF IFS HRES ``oper``, IFS ENS ``enfo``, ``aifs`` single; GEFS ``oper``);
4. merges them in (sorted, duplicates dropped), rewrites the monthly files and their manifest;
5. re-uploads them (``--clobber``), so a Release holds one file per product and month.

Usage: ``python -m meteo.aggregate --dir monthly --lookback 3``
The monthly files present in ``--dir`` are updated in place; new months are created.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .common import manifest_entry, write_nc

PRODUCTS = {  # product -> (file prefix, builder kind)
    "oper": ("ecmwf_oper_uv10_0p5", "ecmwf"),
    "aifs": ("ecmwf_aifs_uv10_0p5", "ecmwf"),
    "enfo": ("ecmwf_enfo_uv10_1p0", "ecmwf"),
    "gefs": ("gefs_oper_uv10_1p0", "gefs"),
}
INITS_PER_DAY = {"oper": (0, 12), "aifs": (0, 12), "enfo": (0,), "gefs": (0,)}


def monthly_path(d: Path, prefix: str, day: pd.Timestamp) -> Path:
    return d / f"{prefix}_{day:%Y%m}.nc"


def present_inits(path: Path) -> set[np.datetime64]:
    if not path.exists():
        return set()
    with xr.open_dataset(path) as ds:
        return set(ds["init"].values.astype("datetime64[h]"))


def fetch_day(product: str, day: pd.Timestamp, tmp: Path) -> Path | None:
    if PRODUCTS[product][1] == "ecmwf":
        from .ecmwf import build_open_month
        files = build_open_month(day.year, day.month, tmp, (product,), [day.day])
    else:
        from .gefs import build_month
        files = build_month("oper", day.year, day.month, tmp, [day.day])
    return files[0] if files else None


def merge_into(monthly: Path, new: Path, attrs: dict) -> None:
    parts = [xr.open_dataset(new).load()]
    if monthly.exists():
        parts.insert(0, xr.open_dataset(monthly).load())
    ds = xr.concat(parts, "init", join="outer")
    _, keep = np.unique(ds["init"].values, return_index=True)
    ds = ds.isel(init=np.sort(keep)).sortby("init")
    for p in parts:
        p.close()
    write_nc(ds, monthly, {**ds.attrs, **attrs})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="monthly")
    ap.add_argument("--lookback", type=int, default=3, help="days before today to (re)check")
    ap.add_argument("--products", nargs="*", default=list(PRODUCTS))
    a = ap.parse_args()
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    today = pd.Timestamp.utcnow().normalize().tz_localize(None)
    touched = set()
    for k in range(a.lookback, 0, -1):
        day = today - pd.Timedelta(days=k)
        for prod in a.products:
            prefix = PRODUCTS[prod][0]
            mp = monthly_path(d, prefix, day)
            want = {np.datetime64(day + pd.Timedelta(hours=h), "h") for h in INITS_PER_DAY[prod]}
            if want <= present_inits(mp):
                continue
            with tempfile.TemporaryDirectory() as td:
                f = fetch_day(prod, day, Path(td))
                if f is None:
                    print(f"{prod} {day:%Y-%m-%d}: not available yet", flush=True)
                    continue
                merge_into(mp, f, {"aggregated": pd.Timestamp.utcnow().isoformat()})
            touched.add(mp)
            print(f"{prod} {day:%Y-%m-%d}: merged into {mp.name}", flush=True)
    for mp in sorted(touched):
        man = mp.with_name(f"manifest_{mp.stem}.json")
        man.write_text(json.dumps([manifest_entry(mp)], indent=1))
    (d / "touched.txt").write_text("\n".join(str(p) for p in sorted(touched)))
    print(f"{len(touched)} monthly file(s) updated")


if __name__ == "__main__":
    main()
