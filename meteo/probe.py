"""Availability scan of a forecast source over a whole period, *before* any bulk download.

For every day (and run) it sends a few cheap HEAD/LIST requests: the first and the last wanted
step (or member) must exist. Output: one line per month (days complete / partial / missing and
the file layout seen) plus a JSON report, so that bulk matrices only cover months that exist and
gaps are known in advance.

    python -m meteo.probe ecmwf_open --products oper aifs enfo wave --start 2023-01-01 --end 2025-12-31
    python -m meteo.probe gefs_oper --start 2023-01-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .common import CFG, expand_steps


def _head(ses, url: str) -> bool:
    try:
        r = ses.head(url, timeout=60)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _ecmwf(prod: str):
    from . import ecmwf_fetch as E
    cfg = CFG["datasets"]["ecmwf_open"]["products"][prod]
    steps = expand_steps(cfg["steps"])

    def check(day, hh):
        for lay in E.LAYOUTS[prod]:
            if _head(E.SES, E._url(day, hh, lay, 0)[:-6] + ".index"):
                last = _head(E.SES, E._url(day, hh, lay, steps[-1])[:-6] + ".index")
                mid = _head(E.SES, E._url(day, hh, lay, steps[len(steps) // 2])[:-6] + ".index")
                return ("ok" if last and mid else "partial"), "/".join(x for x in lay[:2] if x)
        return "missing", ""
    return check, cfg["runs"]


def _gefs(dataset: str):
    from . import gefs as G
    D = CFG["datasets"][dataset]
    steps = expand_steps(D["leads_h"])
    if dataset == "gefs_ctl":
        sub, pat, mems = "pgrb2sp25", "ge{m}.t00z.pgrb2s.0p25.f{s:03d}", ["c00"]
    else:
        sub, pat = "pgrb2ap5", "ge{m}.t00z.pgrb2a.0p50.f{s:03d}"
        mems = ["c00", f"p{D['members']:02d}"]

    def check(day, hh):
        base = f"{G.S3O}/gefs.{day:%Y%m%d}/00/atmos/{sub}/"
        if not _head(G.SES, base + pat.format(m="c00", s=0) + ".idx"):
            return "missing", ""
        ok = all(_head(G.SES, base + pat.format(m=m, s=steps[-1]) + ".idx") for m in mems)
        return ("ok" if ok else "partial"), sub
    return check, [0]


def _reforecast():
    from . import gefs as G

    def check(day, hh):
        try:
            keys = G._list(G.S3R, f"GEFSv12/reforecast/{day:%Y}/{day:%Y%m%d}00/")
        except Exception:  # noqa: BLE001
            return "missing", ""
        mems = {k.split("/")[4] for k in keys if "/Days:1-10/" in k
                and k.rsplit("/", 1)[1].startswith(("ugrd_hgt", "vgrd_hgt"))}
        if not mems:
            return "missing", ""
        return "ok", f"{len(mems)}mem"
    return check, [0]


def _gfswave():
    from . import gfswave as W
    D = CFG["datasets"]["gfs_wave"]
    steps = expand_steps(D["steps"])

    def check(day, hh):
        u = (f"{W.BASE}/gfs.{day:%Y%m%d}/{hh:02d}/wave/gridded/"
             f"gfswave.t{hh:02d}z.global.0p25.f{{:03d}}.grib2.idx")
        if not _head(W.SES, u.format(0)):
            return "missing", ""
        return ("ok" if _head(W.SES, u.format(steps[-1])) else "partial"), "0p25"
    return check, D["runs"]


def probe(name: str, check, runs, days) -> dict:
    jobs = [(d, hh) for d in days for hh in runs]
    with cf.ThreadPoolExecutor(16) as ex:
        res = list(ex.map(lambda j: check(*j), jobs))
    months: dict = defaultdict(lambda: {"ok": 0, "partial": 0, "missing": 0, "layouts": set(),
                                        "bad": []})
    for (d, hh), (st, lay) in zip(jobs, res):
        m = months[f"{d:%Y-%m}"]
        m[st] += 1
        if lay:
            m["layouts"].add(lay)
        if st != "ok":
            m["bad"].append(f"{d:%d}/{hh:02d}{st[0]}")
    out = {}
    for k in sorted(months):
        m = months[k]
        m["layouts"] = sorted(m["layouts"])
        out[k] = m
        print(f"{name:14s} {k}  ok {m['ok']:3d}  partial {m['partial']:3d}  missing "
              f"{m['missing']:3d}  {','.join(m['layouts']):22s} {' '.join(m['bad'][:12])}",
              flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=["ecmwf_open", "gefs_oper", "gefs_ctl", "gefs_reforecast",
                                        "gfs_wave"])
    ap.add_argument("--products", nargs="*", default=["oper", "aifs", "enfo", "wave"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--every", type=int, default=1, help="probe one day in N")
    ap.add_argument("--json", default="probe.json")
    a = ap.parse_args()
    days = list(pd.date_range(a.start, a.end, freq="D"))[::a.every]
    report = {}
    if a.dataset == "ecmwf_open":
        for p in a.products:
            report[f"ecmwf_{p}"] = probe(f"ecmwf_{p}", *_ecmwf(p), days)
    elif a.dataset == "gefs_reforecast":
        report[a.dataset] = probe(a.dataset, *_reforecast(), days)
    elif a.dataset == "gfs_wave":
        report[a.dataset] = probe(a.dataset, *_gfswave(), days)
    else:
        report[a.dataset] = probe(a.dataset, *_gefs(a.dataset), days)
    Path(a.json).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
