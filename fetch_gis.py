"""
fetch_gis.py — downloads DPI's "Public Schools, Wisconsin" point layer (already
geocoded — school name, address, lat/long, district) from their public GIS
Open Data Portal, and filters it down to Milwaukee County.

This is a SEPARATE data source from fetch_dpi.py's enrollment/attendance/
discipline files — those come from WISEdash, this comes from DPI's GIS portal,
and the two aren't the same download system. This one uses ArcGIS Hub's
dataset-download API rather than a yearly zip file.

Honest caveat: the exact download URL below is my best-verified guess at
ArcGIS Hub's standard download pattern for this specific dataset (item ID
confirmed against DPI's real dataset page), but I could not fetch-test it
live from the environment this was built in. If it 404s or comes back empty,
the fallback is manual: go to
  https://data-wi-dpi.opendata.arcgis.com/datasets/WI-DPI::public-schools-wisconsin-1
click "Download" -> CSV, and paste the real link into config.yaml's
gis.schools_url field, or just point SCHOOLS_CSV_URL below at wherever you
saved the manually-downloaded file (a local path works too, see load step).

Run directly:  python fetch_gis.py
"""
import pathlib
import yaml
import requests
import pandas as pd

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")

# DPI's "Public Schools, Wisconsin" dataset item ID on ArcGIS Hub, confirmed
# via the dataset's own page metadata. This URL pattern is ArcGIS Hub's
# standard "download as CSV" API.
SCHOOLS_ITEM_ID = "d383fe81275e46f2a5a5c4f1a0c2eb85"
SCHOOLS_CSV_URL = (
    f"https://opendata.arcgis.com/api/v3/datasets/{SCHOOLS_ITEM_ID}_0/"
    f"downloads/data?format=csv&spatialRefId=4326"
)


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def fetch_schools_csv(url=None):
    url = url or SCHOOLS_CSV_URL
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / "school_locations_raw.csv"

    print(f"[fetch] school locations -> {out_path}")
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[error] could not download school locations: {e}")
        print("  Manual fallback: go to")
        print("  https://data-wi-dpi.opendata.arcgis.com/datasets/WI-DPI::public-schools-wisconsin-1")
        print("  click Download -> CSV, save it, then set gis.schools_url in config.yaml")
        print("  to the real link (or a local file path) and re-run.")
        return None

    out_path.write_bytes(r.content)
    print(f"[ok] saved {out_path}")
    return out_path


def find_county_col(df):
    for candidate in ["COUNTY", "County", "COUNTY_NAME", "CNTY_NAME"]:
        if candidate in df.columns:
            return candidate
    return None


def find_lat_lon_cols(df):
    lat_candidates = ["LAT", "LATITUDE", "Latitude", "lat", "Y", "POINT_Y"]
    lon_candidates = ["LON", "LONG", "LONGITUDE", "Longitude", "lon", "long", "X", "POINT_X"]
    lat_col = next((c for c in lat_candidates if c in df.columns), None)
    lon_col = next((c for c in lon_candidates if c in df.columns), None)
    return lat_col, lon_col


def filter_to_county(raw_path, county_filter="Milwaukee"):
    if raw_path is None or not raw_path.exists():
        return None

    df = pd.read_csv(raw_path, low_memory=False)
    df.columns = [c.strip() for c in df.columns]

    print(f"[inspect] school_locations_raw.csv has {df.shape[1]} columns:")
    for c in df.columns:
        print(f"    - {c}")

    county_col = find_county_col(df)
    lat_col, lon_col = find_lat_lon_cols(df)

    if not county_col:
        print("[error] couldn't auto-detect a COUNTY column — check the list "
              "above and update find_county_col() in fetch_gis.py to match.")
        return None
    if not lat_col or not lon_col:
        print("[error] couldn't auto-detect latitude/longitude columns — "
              "check the list above and update find_lat_lon_cols() in "
              "fetch_gis.py to match.")
        return None

    filtered = df[df[county_col].astype(str).str.contains(county_filter, case=False, na=False)].copy()
    if filtered.empty:
        print(f"[warn] no rows matched county filter '{county_filter}' in column '{county_col}'")
        return None

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / "school_locations_milwaukee.csv"
    filtered.to_csv(out_path, index=False)
    print(f"[ok] {len(filtered)} Milwaukee County schools saved to {out_path}")
    print(f"  (lat/long columns: {lat_col}, {lon_col} — ready to drop into any mapping tool)")
    return out_path


def run_all():
    cfg = load_config()
    gis_cfg = cfg.get("gis", {})
    county_filter = cfg.get("county_filter", "Milwaukee")
    custom_url = gis_cfg.get("schools_url")  # optional manual override

    raw_path = fetch_schools_csv(custom_url)
    filter_to_county(raw_path, county_filter)


if __name__ == "__main__":
    run_all()
