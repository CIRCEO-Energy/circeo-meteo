"""Daily prospective archive of the forecasts (run by circeo-meteo-forecasts/.github/workflows/daily.yml).

For each product and each of the last ``--lookback`` days:

* ``file: monthly`` products (ECMWF HRES/AIFS/waves, GEFS control, GFS-Wave): the current monthly
  file (downloaded beforehand into ``--dir``) is checked for the day's initialisations; missing
  ones are fetched and merged in (sorted, duplicates dropped) and the monthly file is rewritten;
* ``file: daily`` products (ECMWF ENS, GEFS ensemble): the day's file is fetched only if it is not
  already listed in ``--existing`` (asset names of the Releases).

Touched/new files and their manifests are listed in ``<dir>/touched.txt`` for upload.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .common import CFG, manifest_entry, res_tag, write_nc

_E = CFG["datasets"]["ecmwf_open"]["products"]
_D = CFG["datasets"]
# product -> (file stem, kind, runs, granularity)
PRODUCTS = {
    **{p: (f"ecmwf_{p}_{'waves' if p == 'wave' else 'uv10'}_{res_tag(c['res'])}", "ecmwf",
           tuple(c["runs"]), c.get("file", "monthly")) for p, c in _E.items()},
    "gefs_oper": (f"gefs_oper_uv10_{res_tag(_D['gefs_oper']['res'])}", "gefs", (0,),
                  _D["gefs_oper"].get("file", "monthly")),
    "gefs_ctl": (f"gefs_ctl_uv10_{res_tag(_D['gefs_ctl']['res'])}", "gefs", (0,),
                 _D["gefs_ctl"].get("file", "monthly")),
    "gfs_wave": (f"gfs_wave_{res_tag(_D['gfs_wave']['res'])}", "gfswave",
                 tuple(_D["gfs_wave"]["runs"]), _D["gfs_wave"].get("file", "monthly")),
}


def present_inits(path: Path) -> set:
    if not path.exists():
        return set()
    with xr.open_dataset(path) as ds:
        return set(ds["init"].values.astype("datetime64[h]"))


def fetch_day(product: str, day: pd.Timestamp, tmp: Path) -> list[Path]:
    kind = PRODUCTS[product][1]
    if kind == "ecmwf":
        from .ecmwf import build_open_month
        return build_open_month(day.year, day.month, tmp, (product,), [day.day])
    if kind == "gfswave":
        from .gfswave import build_month as gw
        return gw(day.year, day.month, tmp, [day.day])
    from .gefs import build_month
    return build_month(product.split("_")[1], day.year, day.month, tmp, [day.day])


def merge_into(monthly: Path, new: Path) -> None:
    parts = [xr.open_dataset(new).load()]
    if monthly.exists():
        parts.insert(0, xr.open_dataset(monthly).load())
    ds = xr.concat(parts, "init", join="outer")
    _, keep = np.unique(ds["init"].values, return_index=True)
    ds = ds.isel(init=np.sort(keep)).sortby("init")
    for p in parts:
        p.close()
    write_nc(ds, monthly, {**ds.attrs, "aggregated": pd.Timestamp.utcnow().isoformat()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="monthly")
    ap.add_argument("--existing", default="", help="file with existing asset names")
    ap.add_argument("--lookback", type=int, default=3)
    ap.add_argument("--products", nargs="*", default=list(PRODUCTS))
    a = ap.parse_args()
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    existing = set(Path(a.existing).read_text().split()) if a.existing else set()
    today = pd.Timestamp.utcnow().normalize().tz_localize(None)
    touched = []
    for k in range(a.lookback, 0, -1):
        day = today - pd.Timedelta(days=k)
        for prod in a.products:
            stem, _, runs, gran = PRODUCTS[prod]
            if gran == "daily":
                name = f"{stem}_{day:%Y%m%d}.nc"
                if name in existing or (d / name).exists():
                    continue
                files = fetch_day(prod, day, d)
                touched += [f for f in files if f.name == name]
                print(f"{prod} {day:%Y-%m-%d}: {'new ' + name if files else 'not available yet'}",
                      flush=True)
                continue
            mp = d / f"{stem}_{day:%Y%m}.nc"
            want = {np.datetime64(day + pd.Timedelta(hours=h), "h") for h in runs}
            if want <= present_inits(mp):
                continue
            with tempfile.TemporaryDirectory() as td:
                files = fetch_day(prod, day, Path(td))
                if not files:
                    print(f"{prod} {day:%Y-%m-%d}: not available yet", flush=True)
                    continue
                merge_into(mp, files[0])
            if mp not in touched:
                touched.append(mp)
            print(f"{prod} {day:%Y-%m-%d}: merged into {mp.name}", flush=True)
    out = []
    for f in touched:
        man = f.with_name(f"manifest_{f.stem}.json")
        man.write_text(json.dumps([manifest_entry(f)], indent=1))
        out += [str(f), str(man)]
    (d / "touched.txt").write_text("\n".join(out))
    print(f"{len(touched)} file(s) new or updated")


if __name__ == "__main__":
    main()
