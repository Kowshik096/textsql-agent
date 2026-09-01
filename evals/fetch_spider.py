"""
fetch_spider.py — download the Spider text-to-SQL dev set for the external
validation (Step 6 of NEXT_FIVE_DAYS).

    python evals/fetch_spider.py

Lands in evals/external/spider/ as <db>/<db>.sqlite files plus dev.json. The
suite is self-written, and a guard only tested against attacks its author
imagined has a blind spot shaped like its author - same argument for golden
datasets. This fetches someone else's questions and gold SQL so accuracy gets
an external reference point.

Mirrors are tried in order; if all fail, the script says exactly what to
place where instead of leaving a half-extracted tree behind.
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "evals" / "external"
SPIDER_DIR = TARGET / "spider"

URLS = [
    # Official Spider.zip on Google Drive; the file id is linked from
    # https://yale-lily.github.io/spider. The usercontent endpoint serves
    # public files without cookie handling.
    "https://drive.usercontent.google.com/download"
    "?id=1403EGqzIDoHMdQF4c9Bkyl7dZLZ5Wt6J&export=download&confirm=t",
    # Text-only parquet mirror - pairs without databases, useful as a
    # cross-check of dev.json but NOT sufficient by itself.
    "https://huggingface.co/datasets/xlangai/spider/resolve/main/"
    "spider/validation-00000-of-00001.parquet",
]

MANUAL = """
No mirror worked. Place the official spider.zip manually:

  1. Download from https://yale-lily.github.io/spider (the Spider.zip link)
  2. Put it at evals/external/spider.zip
  3. Re-run this script - it will extract an existing zip before downloading.

"""


def find_payload(root: Path) -> tuple | None:
    """Locate dev.json + database/ wherever the zip put them."""
    dev = list(root.rglob("dev.json"))
    dbs = [p for p in root.rglob("*") if p.is_dir() and p.name == "database"]
    if dev and dbs:
        return dev[0], dbs[0]
    return None


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    payload = find_payload(SPIDER_DIR) if SPIDER_DIR.exists() else None

    if payload is None:
        staged = TARGET / "spider.zip"
        downloaded = False
        for url in URLS:
            print(f"trying {url} ...", flush=True)
            try:
                with requests.get(url, timeout=600, stream=True) as r:
                    r.raise_for_status()
                    total = 0
                    with open(staged, "wb") as fh:
                        for chunk in r.iter_content(chunk_size=1 << 16):
                            fh.write(chunk)
                            total += len(chunk)
                # Drive can serve an HTML consent page with status 200; a zip
                # starts with the PK magic, so verify rather than trust.
                if staged.read_bytes()[:2] != b"PK":
                    print("  response was not a zip (consent page?)")
                    continue
                print(f"  downloaded {total / 1e6:.1f} MB")
                downloaded = True
                break
            except requests.RequestException as e:
                print(f"  failed: {e}")

        if not downloaded and not staged.exists():
            print(MANUAL)
            return 1

        extract_to = TARGET / "_extract"
        if extract_to.exists():
            shutil.rmtree(extract_to)
        with zipfile.ZipFile(staged) as zf:
            zf.extractall(extract_to)
        found = find_payload(extract_to)
        if found is None:
            print(f"zip extracted but no dev.json/database under {extract_to}")
            return 1

        SPIDER_DIR.mkdir(parents=True, exist_ok=True)
        dev, dbdir = found
        shutil.move(str(dev), SPIDER_DIR / "dev.json")
        shutil.move(str(dbdir), SPIDER_DIR / "database")
        shutil.rmtree(extract_to, ignore_errors=True)

    dev_file = SPIDER_DIR / "dev.json"
    db_root = SPIDER_DIR / "database"
    dbs = sorted(p.name for p in db_root.iterdir() if p.is_dir())
    n_questions = len(json.loads(dev_file.read_text(encoding="utf-8")))
    print(f"\nready: {n_questions} dev questions across {len(dbs)} databases")
    print("databases:", ", ".join(dbs[:10]) + (" ..." if len(dbs) > 10 else ""))
    print("next: python evals/make_external_benchmark.py --db car_1 --total 25")
    return 0


if __name__ == "__main__":
    sys.exit(main())
