"""Climate indices (monthly), one JSON file: ONI / Nino3.4 (NOAA CPC), NAO (CPC), AMO (NOAA PSL).

{"oni": {"YYYY-SSS": value (3-month season, e.g. "2010-JJA")}, "nino34": {"YYYY-MM": anomaly},
 "nao": {"YYYY-MM": value}, "amo": {"YYYY-MM": value}, "sources": {...}}
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

SRC = {
    "oni": "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt",
    "nino34": "https://www.cpc.ncep.noaa.gov/data/indices/ersst5.nino.mth.91-20.ascii",
    "nao": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/pna/norm.nao.monthly.b5001.current.ascii",
    "amo": "https://psl.noaa.gov/data/correlation/amon.us.data",
}
SEAS = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]


def _get(url):
    r = requests.get(url, timeout=120, headers={"User-Agent": "circeo-meteo"})
    r.raise_for_status()
    return r.text


def build(out: Path) -> list[Path]:
    res, errs = {"sources": SRC}, {}
    for k, url in SRC.items():
        try:
            txt = _get(url)
        except Exception as exc:  # noqa: BLE001 - publish what is reachable
            errs[k] = repr(exc)[:200]
            continue
        d = {}
        if k == "oni":  # SEAS YR TOTAL ANOM
            for ln in txt.splitlines()[1:]:
                p = ln.split()
                if len(p) == 4 and p[0] in SEAS:
                    d[f"{p[1]}-{p[0]}"] = float(p[3])
        elif k == "nino34":  # YR MON NINO1+2 ANOM NINO3 ANOM NINO4 ANOM NINO3.4 ANOM
            for ln in txt.splitlines()[1:]:
                p = ln.split()
                if len(p) == 10:
                    d[f"{p[0]}-{int(p[1]):02d}"] = float(p[9])
        elif k == "nao":  # YR MON VALUE
            for ln in txt.splitlines():
                p = ln.split()
                if len(p) == 3 and p[0].isdigit():
                    d[f"{p[0]}-{int(p[1]):02d}"] = float(p[2])
        else:  # PSL format: header line "y0 y1", then YEAR + 12 values, missing -99.99
            for ln in txt.splitlines()[1:]:
                p = ln.split()
                if len(p) == 13 and p[0].isdigit():
                    for m, v in enumerate(p[1:], 1):
                        if float(v) > -99:
                            d[f"{p[0]}-{m:02d}"] = float(v)
        res[k] = d
    res["errors"] = errs
    f = out / "indices_monthly.json"
    f.write_text(json.dumps(res))
    print({k: len(v) for k, v in res.items() if isinstance(v, dict)}, errs, flush=True)
    return [f]
