"""Backup mirror: GitHub Release assets -> Google Drive (rclone), run on GitHub Actions.

Every asset of every Release of ``--github`` goes to ``<remote>:<root>/<repo>/<tag>/<name>``.
Files already present with the same size are skipped (incremental, daily schedule). Each file is
streamed from the public download URL straight to Drive (``rclone copyurl``, no runner disk), a
few in parallel; the job stops cleanly after ``--max-minutes`` and the next run continues.
Drive accepts about 750 GB of uploads per user and day: beyond, rclone errors and the next run
resumes.

    RCLONE_CONFIG=rclone.conf python -m meteo.mirror_gdrive --github CIRCEO-Energy/circeo-meteo
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import time


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


def rclone(*args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["rclone", *args], capture_output=True, text=True, check=check)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--github", required=True)
    ap.add_argument("--remote", default="gdrive")
    ap.add_argument("--root", default="CIRCEO-meteo-backup")
    ap.add_argument("--release", help="only this Release tag")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--max-minutes", type=float, default=330.0)
    a = ap.parse_args()
    t_end = time.time() + 60 * a.max_minutes
    base = f"{a.remote}:{a.root}/{a.github.split('/')[1]}"
    r = rclone("lsjson", "-R", "--files-only", base, check=False)
    have = {f["Path"]: f["Size"] for f in json.loads(r.stdout or "[]")} if r.returncode == 0 else {}
    todo = []
    for rel in gh_api(f"repos/{a.github}/releases"):
        if a.release and rel["tag_name"] != a.release:
            continue
        for asset in gh_api(f"repos/{a.github}/releases/{rel['id']}/assets"):
            p = f"{rel['tag_name']}/{asset['name']}"
            if have.get(p) != asset["size"]:
                todo.append((p, asset))
    tot = sum(x[1]["size"] for x in todo)
    print(f"{len(todo)} file(s) to copy, {tot / 1e9:.1f} GB ({len(have)} already on Drive)",
          flush=True)

    def one(item):
        p, asset = item
        if time.time() > t_end:
            return p, "skipped (time)"
        for k in range(3):
            r = rclone("copyurl", asset["browser_download_url"], f"{base}/{p}",
                       "--retries", "3", "--low-level-retries", "10", check=False)
            if r.returncode == 0:
                return p, "ok"
            if "userRateLimitExceeded" in r.stderr or "quotaExceeded" in r.stderr:
                return p, "daily Drive upload quota reached"
            time.sleep(30 * (k + 1))
        return p, f"FAILED {r.stderr.strip()[-200:]}"

    n_ok, n_bytes = 0, 0
    sizes = dict((p, s["size"]) for p, s in todo)
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        for p, st in ex.map(one, todo):
            if st == "ok":
                n_ok += 1
                n_bytes += sizes[p]
            if st != "ok" or n_ok % 25 == 0:
                print(f"  {p}: {st} ({n_ok} ok, {n_bytes / 1e9:.1f} GB)", flush=True)
    print(f"copied {n_ok}/{len(todo)} file(s), {n_bytes / 1e9:.1f} GB; "
          f"{'complete' if n_ok == len(todo) else 'to be continued'}")


if __name__ == "__main__":
    main()
