"""Upload output files to the Releases of a repository (tag per file from config.yaml).

Usage (GitHub Actions, GH_TOKEN set): ``python -m meteo.publish --repo OWNER/NAME out/*``
Creates missing Releases (notes: docs/release_notes.md) and uploads with --clobber.
"""
from __future__ import annotations

import argparse
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from .common import ROOT, release_for


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    groups = defaultdict(list)
    for f in a.files:
        p = Path(f)
        if p.is_file() and p.suffix in (".nc", ".json"):
            groups[release_for(p)].append(str(p))
    notes = ROOT / "docs" / "release_notes.md"
    for tag, files in sorted(groups.items()):
        if subprocess.run(["gh", "release", "view", tag, "-R", a.repo], check=False,
                          capture_output=True).returncode != 0:
            subprocess.run(["gh", "release", "create", tag, "-R", a.repo, "--title", tag,
                            "--notes-file", str(notes)], check=False)
        for i in range(0, len(files), 20):
            # the API rate limit (1000 requests/h per repository for GITHUB_TOKEN) is shared by
            # all workflows: wait and retry rather than losing hours of extraction
            for k in range(8):
                r = subprocess.run(["gh", "release", "upload", tag, *files[i:i + 20], "-R",
                                    a.repo, "--clobber"], capture_output=True, text=True)
                if r.returncode == 0:
                    break
                print(f"  upload failed ({r.stderr.strip()[:160]}), retry in {5 * (k + 1)} min",
                      flush=True)
                time.sleep(300 * (k + 1))
            else:
                raise SystemExit(f"upload to {tag} failed")
        print(f"{tag}: {len(files)} file(s)")


if __name__ == "__main__":
    main()
