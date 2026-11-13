"""Command line entry point used by the GitHub Actions workflows (and runnable locally).

Examples::

    python -m meteo.build era5 --year 2015                       # one yearly file
    python -m meteo.build era5 --year 2015 --months 1            # smoke test (one month)
    python -m meteo.build gefs_reforecast --year 2010 --month 3
    python -m meteo.build gefs_oper --year 2023 --month 1
    python -m meteo.build ecmwf_hres_wb2 --year 2019 --month 7 --accept-licence
    python -m meteo.build ecmwf_open --year 2025 --month 2 [--days 1 2]

Writes NetCDF files and ``manifest_<dataset>_<tag>.json`` (sha256 per file) into ``--out``.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .common import manifest_entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=["era5", "era5_waves", "era5_sst", "gefs_reforecast",
                                        "gefs_oper", "gefs_ctl", "gfs_wave", "ecmwf_hres_wb2", "ecmwf_open",
                                        "cmems_phy_reanalysis", "cmems_phy_forecast",
                                        "cmems_wav_reanalysis"])
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--month", type=int)
    ap.add_argument("--months", type=int, nargs="*")
    ap.add_argument("--days", type=int, nargs="*")
    ap.add_argument("--products", nargs="*", default=["oper", "aifs", "enfo", "wave"])
    ap.add_argument("--accept-licence", action="store_true")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if a.dataset.startswith("era5"):
        from .era5 import build_year
        files = build_year(a.year, out, a.months, a.dataset)
    elif a.dataset in ("gefs_reforecast", "gefs_oper", "gefs_ctl"):
        from .gefs import build_month
        files = build_month(a.dataset.split("_")[1], a.year, a.month, out, a.days)
    elif a.dataset == "gfs_wave":
        from .gfswave import build_month as gw
        files = gw(a.year, a.month, out, a.days)
    elif a.dataset.startswith("cmems"):
        from .cmems import archive_forecast
        from .cmems import build_month as cm
        files = archive_forecast(out) if a.dataset == "cmems_phy_forecast" else \
            cm(a.dataset, a.year, a.month, out)
    elif a.dataset == "ecmwf_hres_wb2":
        from .ecmwf import build_wb2_month
        files = build_wb2_month(a.year, a.month, out, a.accept_licence)
    else:
        from .ecmwf import build_open_month
        files = build_open_month(a.year, a.month, out, tuple(a.products), a.days)
    for f in files:  # one manifest per data file (same Release as the file)
        (out / f"manifest_{f.stem}.json").write_text(
            json.dumps([manifest_entry(f, dataset=a.dataset)], indent=1))
    print(f"{len(files)} file(s), {sum(f.stat().st_size for f in files) / 2**20:.1f} MiB, "
          f"{(time.time() - t0) / 60:.1f} min")
    if not files:
        raise SystemExit("no data produced")


if __name__ == "__main__":
    main()
