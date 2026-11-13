"""Agent-side access to the meteo archive (GitHub Releases of the public circeo-meteo repository).

Usage (inside the router repository's environment; uses the `gh` CLI credentials)::

    python fetch_meteo.py --repo <owner>/circeo-meteo --release era5-uv10-0p5-v1 \
        --pattern 'era5_uv10_0p5_201*.nc' --dest data/meteo
    python fetch_meteo.py --repo ... --list                     # releases and asset counts

Files are cached: an asset already present with the right size is not downloaded again, and the
sha256 is checked against the ``manifest_*.json`` assets of the same release when available.
Nothing passes through the conversation: downloads cost no tokens.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def gh_json(*args: str):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 22), b""):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--release")
    ap.add_argument("--pattern", default="*.nc")
    ap.add_argument("--dest", default="data/meteo")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for r in gh_json("api", f"repos/{a.repo}/releases", "--paginate"):
            n = len(r["assets"])
            gb = sum(x["size"] for x in r["assets"]) / 2**30
            print(f"{r['tag_name']:40s} {n:5d} assets {gb:7.2f} GiB")
        return
    rel = gh_json("api", f"repos/{a.repo}/releases/tags/{a.release}")
    dest = Path(a.dest) / a.release
    dest.mkdir(parents=True, exist_ok=True)
    assets = {x["name"]: x for x in rel["assets"]}
    sums = {}
    for name in [n for n in assets if n.startswith("manifest_")]:
        subprocess.run(["gh", "release", "download", a.release, "-R", a.repo, "-p", name,
                        "-D", str(dest), "--clobber"], check=True)
        for e in json.loads((dest / name).read_text()):
            sums[e["file"]] = e["sha256"]
    want = [n for n in assets if fnmatch.fnmatch(n, a.pattern) and not n.startswith("manifest_")]
    for name in sorted(want):
        f = dest / name
        if f.exists() and f.stat().st_size == assets[name]["size"]:
            continue
        subprocess.run(["gh", "release", "download", a.release, "-R", a.repo, "-p", name,
                        "-D", str(dest), "--clobber"], check=True)
        if name in sums and sha256(f) != sums[name]:
            sys.exit(f"checksum mismatch: {name}")
        print(f"ok {name} ({assets[name]['size'] / 2**20:.1f} MiB)")
    print(f"{len(want)} file(s) in {dest}")


if __name__ == "__main__":
    main()
