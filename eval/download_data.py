"""Download the two M5 files the evaluation needs into data/m5_raw/.

Source: Nixtla's public copy of the M5 (Walmart) competition data on GitHub (~50 MB zip).
Only calendar.csv and sales_train_evaluation.csv are kept.
"""
from __future__ import annotations

import io
import urllib.request
import zipfile
from pathlib import Path

SOURCE_URL = "https://github.com/Nixtla/m5-forecasts/raw/main/datasets/m5.zip"
NEEDED = ("calendar.csv", "sales_train_evaluation.csv")
DEST = Path(__file__).resolve().parent.parent / "data" / "m5_raw"


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    if all((DEST / name).exists() for name in NEEDED):
        print(f"M5 data already in {DEST}")
        return
    print(f"Downloading {SOURCE_URL} (about 50 MB)...")
    with urllib.request.urlopen(SOURCE_URL, timeout=600) as resp:
        archive = zipfile.ZipFile(io.BytesIO(resp.read()))
    members = {Path(m).name: m for m in archive.namelist()}
    for name in NEEDED:
        if name not in members:
            raise SystemExit(f"{name} not found in the downloaded archive")
        (DEST / name).write_bytes(archive.read(members[name]))
        print(f"Saved {DEST / name}")


if __name__ == "__main__":
    main()
