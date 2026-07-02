"""
fetch_dpi.py — downloads DPI's yearly ZIP files for each source in config.yaml,
unzips them, picks out the real data CSV (DPI sometimes bundles a layout/readme
file in the same zip), tags rows with the school year if the file doesn't
already have one, and stitches all years into one combined CSV per source:
  data/raw/<source>_raw.csv

Run directly:  python fetch_dpi.py
"""
import io
import pathlib
import zipfile

import pandas as pd
import requests
import yaml

RAW_DIR = pathlib.Path("data/raw")
BASE_URL = "https://dpi.wi.gov/sites/default/files/wise/downloads"

# Filenames inside the zip that are docs, not data — skip these when picking
# the real CSV.
SKIP_NAME_HINTS = ("layout", "readme", "dictionary", "notes", "codebook")


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def pick_data_csv(zf: zipfile.ZipFile):
    """Given an open ZipFile, return the name of the member that looks like
    the actual data file (largest CSV, excluding obvious doc files)."""
    candidates = [
        info for info in zf.infolist()
        if info.filename.lower().endswith(".csv")
        and not any(hint in info.filename.lower() for hint in SKIP_NAME_HINTS)
    ]
    if not candidates:
        return None
    # pick the biggest file — the real data table, not a small lookup file
    best = max(candidates, key=lambda i: i.file_size)
    return best.filename


def fetch_year(url_prefix: str, year: str) -> pd.DataFrame | None:
    url = f"{BASE_URL}/{url_prefix}_{year}.zip"
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"    [error] {year}: could not download {url} ({e})")
        return None

    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile:
        print(f"    [error] {year}: {url} did not return a valid zip file")
        return None

    member = pick_data_csv(zf)
    if member is None:
        print(f"    [error] {year}: no CSV found inside {url}")
        return None

    with zf.open(member) as f:
        df = pd.read_csv(f, low_memory=False)

    df.columns = [c.strip() for c in df.columns]
    print(f"    [ok] {year}: {len(df)} rows from {member}")
    return df


def fetch_source(name: str, src: dict, years: list) -> pd.DataFrame:
    print(f"[fetch] {name}")
    year_col = src.get("year_col", "SCHOOL_YEAR")
    frames = []
    for year in years:
        df = fetch_year(src["url_prefix"], year)
        if df is None:
            continue
        # tag with school year if the file doesn't already have one
        if year_col not in df.columns:
            df[year_col] = year
        frames.append(df)

    if not frames:
        print(f"  [skip] {name}: no years fetched successfully")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)
    return combined


def fetch_all(config):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    years = config.get("years", [])
    if not years:
        print("[error] no `years` list set in config.yaml")
        return

    for name, src in config["sources"].items():
        combined = fetch_source(name, src, years)
        if combined.empty:
            continue
        out_path = RAW_DIR / f"{name}_raw.csv"
        combined.to_csv(out_path, index=False)
        print(f"  [saved] {out_path} — {len(combined)} total rows across {len(years)} years")

    print("Done fetching.")


if __name__ == "__main__":
    cfg = load_config()
    fetch_all(cfg)
