"""Backup mirror: GitHub Release assets -> a public Hugging Face dataset repository.

Every asset of every Release of ``--github`` is mirrored to ``<release tag>/<asset name>`` in the
dataset ``--hf`` (created if missing). Assets already present with the same size are skipped, so
the job is incremental and can be re-run at will (daily schedule). Files are streamed through the
runner disk in batches (``--batch-gb``) and committed one batch at a time; the job stops cleanly
after ``--max-minutes`` and the next run continues.

    HF_TOKEN=... python -m meteo.mirror_hf --github boris-circeo/circeo-meteo-forecasts \
        --hf <user>/circeo-meteo-forecasts [--release TAG]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

CARD = """---
license: other
license_name: mixed-open-data
pretty_name: {name}
tags: [weather, wind, ocean-waves, ocean-currents, forecast, reanalysis, era5, ecmwf, gefs]
---
# {name}

Backup mirror of the Release assets of
[github.com/{github}](https://github.com/{github}) (CIRCEO weather archive for routing research),
one folder per Release tag. The GitHub Releases are the primary copy; this mirror is refreshed
daily by `meteo/mirror_hf.py` (repository boris-circeo/circeo-meteo). Each NetCDF file has a
`manifest_<stem>.json` with its SHA-256, source and processing details.

Domain 20-75 N, 80 W-15 E. Variables are stored as packed int16 (scale/offset in the attributes).

## Sources and licences (attribution required)
* ERA5 (Copernicus Climate Change Service, ECMWF), via the ARCO-ERA5 public copy: Copernicus licence.
  "Contains modified Copernicus Climate Change Service information".
* Copernicus Marine Service (GLORYS12, global analysis-forecast): Copernicus Marine licence.
  "Generated using E.U. Copernicus Marine Service Information".
* ECMWF open data (IFS HRES, ENS, AIFS, wave model): CC-BY-4.0, (c) ECMWF.
* NOAA GEFS / GEFSv12 reforecast / GFS-Wave (NOAA Open Data Dissemination): public domain (US Gov.).

Derived files are provided as is, without warranty.
"""


def gh_api(path: str):
    out, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        for k in range(5):
            r = subprocess.run(["gh", "api", f"{path}{sep}per_page=100&page={page}"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                break
            time.sleep(20 * (k + 1))
        else:
            raise RuntimeError(r.stderr[:300])
        got = json.loads(r.stdout)
        out += got
        if len(got) < 100:
            return out
        page += 1


def download(url: str, dest: Path, size: int):
    for k in range(5):
        try:
            with urllib.request.urlopen(url, timeout=900) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, 16 << 20)
            if dest.stat().st_size == size:
                return
        except Exception as exc:  # noqa: BLE001
            print(f"  download retry {k + 1}: {exc!r:.120}", flush=True)
        time.sleep(15 * (k + 1))
    raise RuntimeError(f"download failed: {url}")


def main():
    from huggingface_hub import CommitOperationAdd, HfApi

    ap = argparse.ArgumentParser()
    ap.add_argument("--github", required=True)
    ap.add_argument("--hf", required=True, help="Hugging Face dataset repo id (user/name)")
    ap.add_argument("--release", help="only this Release tag")
    ap.add_argument("--batch-gb", type=float, default=8.0)
    ap.add_argument("--max-minutes", type=float, default=320.0)
    ap.add_argument("--tmp", default="mirror_tmp")
    a = ap.parse_args()
    t_end = time.time() + 60 * a.max_minutes
    api = HfApi(token=os.environ["HF_TOKEN"])
    api.create_repo(a.hf, repo_type="dataset", private=False, exist_ok=True)
    have = {}
    for f in api.list_repo_tree(a.hf, repo_type="dataset", recursive=True):
        if hasattr(f, "size"):
            have[f.path] = f.size
    if "README.md" not in have:
        name = a.github.split("/")[1]
        api.upload_file(path_or_fileobj=CARD.format(name=name, github=a.github).encode(),
                        path_in_repo="README.md", repo_id=a.hf, repo_type="dataset",
                        commit_message="dataset card")
    todo = []
    for rel in gh_api(f"repos/{a.github}/releases"):
        if a.release and rel["tag_name"] != a.release:
            continue
        for asset in gh_api(f"repos/{a.github}/releases/{rel['id']}/assets"):
            p = f"{rel['tag_name']}/{asset['name']}"
            if have.get(p) != asset["size"]:
                todo.append((p, asset))
    tot = sum(x[1]["size"] for x in todo)
    print(f"{len(todo)} file(s) to mirror, {tot / 1e9:.1f} GB ({len(have)} already on the Hub)",
          flush=True)
    tmp = Path(a.tmp)
    done_b, i = 0, 0
    while i < len(todo) and time.time() < t_end:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        ops, size = [], 0
        while i < len(todo) and size < a.batch_gb * 1e9 and time.time() < t_end:
            p, asset = todo[i]
            local = tmp / p.replace("/", "__")
            download(asset["browser_download_url"], local, asset["size"])
            ops.append(CommitOperationAdd(path_in_repo=p, path_or_fileobj=str(local)))
            size += asset["size"]
            i += 1
        for k in range(5):
            try:
                api.create_commit(a.hf, repo_type="dataset", operations=ops,
                                  commit_message=f"mirror {len(ops)} file(s) from {a.github}")
                break
            except Exception as exc:  # noqa: BLE001  (rate limits, transient errors)
                print(f"  commit retry {k + 1}: {exc!r:.200}", flush=True)
                time.sleep(60 * (k + 1))
        else:
            raise RuntimeError("commit failed")
        done_b += size
        print(f"  committed {len(ops)} file(s), {size / 1e9:.1f} GB "
              f"(total {done_b / 1e9:.1f} / {tot / 1e9:.1f} GB)", flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"mirrored {i}/{len(todo)} file(s); {'complete' if i == len(todo) else 'to be continued'}")


if __name__ == "__main__":
    main()
