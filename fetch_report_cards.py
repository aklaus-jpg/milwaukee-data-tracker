"""
fetch_report_cards.py — downloads DPI's official School and District Report
Card data (overall accountability score, 1-5 star rating, and the four
priority areas: Achievement, Growth, Target Group Outcomes, On-Track to
Graduation) and filters to Milwaukee County.

This is a THIRD, separate DPI data source — not WISEdash (fetch_dpi.py) and
not the GIS portal (fetch_gis.py). Report cards live on their own portal:
  https://apps6.dpi.wi.gov/reportcards

Run directly:  python fetch_report_cards.py
"""
import io
import pathlib
import yaml
import requests
import pandas as pd

import archive_raw

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")
BASE_URL = "https://dpi.wi.gov/sites/default/files/imce/accountability/xls"

YEARS = ["2018-19", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
HEADER_ROW = 0

# Report card files have no COUNTY column at all — fall back to matching
# against the actual, fixed list of Milwaukee County public school districts.
# This list doesn't change (district boundaries are stable), so it's safe to
# hardcode. Confirmed against the districts that showed up in the real
# WISEdash county-filtered runs earlier in this project, plus the remaining
# Milwaukee County districts.
MILWAUKEE_COUNTY_DISTRICTS = [
    "Milwaukee", "Cudahy", "Fox Point J2", "Franklin Public",
    "Glendale-River Hills", "Greendale", "Greenfield",
    "Maple Dale-Indian Hill", "Nicolet UHS", "Oak Creek-Franklin Joint",
    "St. Francis", "Saint Francis", "Shorewood", "South Milwaukee",
    "Wauwatosa", "West Allis-West Milwaukee", "Whitefish Bay", "Whitnall",
]


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def fetch_year(level, year):
    """level is 'school' or 'district'."""
    url = f"{BASE_URL}/{year}_{level}_reportcard_data.xlsx"
    try:
        r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"    [error] {year}: could not download {url} ({e})")
        return None

    content = io.BytesIO(r.content)
    try:
        sheet_names = pd.ExcelFile(content).sheet_names
    except Exception as e:
        print(f"    [error] {year}: could not open as Excel workbook ({e})")
        return None

    # The real data usually isn't on the first sheet — government workbooks
    # often lead with cover/description sheets that have a couple of prose
    # columns. The real data table has far more columns, so score every
    # sheet with actual rows by column count and keep the widest one.
    candidates = []
    for sheet in sheet_names:
        try:
            df = pd.read_excel(content, sheet_name=sheet, header=HEADER_ROW)
        except Exception:
            continue
        if len(df) > 0:
            candidates.append((sheet, df))

    if not candidates:
        print(f"    [error] {year}: no sheet with any rows found "
              f"(sheets present: {sheet_names})")
        return None

    best_sheet, best_df = max(candidates, key=lambda pair: pair[1].shape[1])
    if best_sheet != sheet_names[0]:
        print(f"    [note] {year}: used sheet '{best_sheet}' "
              f"({best_df.shape[1]} columns, widest of {len(candidates)} candidate sheets)")

    best_df.columns = [str(c).strip() for c in best_df.columns]
    print(f"    [ok] {year}: {len(best_df)} rows, {best_df.shape[1]} columns")
    return best_df


def fetch_level(level):
    print(f"[fetch] report cards ({level})")
    frames = []
    for year in YEARS:
        df = fetch_year(level, year)
        if df is None:
            continue
        if "SCHOOL_YEAR" not in df.columns and "School Year" not in df.columns:
            df["SCHOOL_YEAR"] = year
        frames.append(df)

    if not frames:
        print(f"  [skip] {level}: no years fetched successfully")
        return None

    combined = pd.concat(frames, ignore_index=True, sort=False)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"report_card_{level}_raw.csv"
    combined.to_csv(out_path, index=False)
    print(f"  [saved] {out_path} — {len(combined)} total rows across {len(YEARS)} years")
    archive_raw.archive_raw(out_path)  # dated, checksummed provenance copy
    return out_path


def find_county_col(df):
    for candidate in ["COUNTY", "County", "COUNTY_NAME", "CNTY_NAME"]:
        if candidate in df.columns:
            return candidate
    return None


def find_district_col(df):
    for candidate in ["District Name", "DISTRICT_NAME", "District_Name", "LEA Name"]:
        if candidate in df.columns:
            return candidate
    return None


def filter_to_county(raw_path, level, county_filter="Milwaukee"):
    if raw_path is None or not raw_path.exists():
        return None

    df = pd.read_csv(raw_path, low_memory=False)
    county_col = find_county_col(df)

    if county_col:
        filtered = df[df[county_col].astype(str).str.contains(county_filter, case=False, na=False)].copy()
    else:
        district_col = find_district_col(df)
        if not district_col:
            print(f"[warn] {level}: no COUNTY or District Name column found. Columns:")
            for c in df.columns:
                print(f"    - {c}")
            print(f"  Run: python inspect_report_card.py {raw_path}")
            return None
        pattern = "|".join(MILWAUKEE_COUNTY_DISTRICTS)
        filtered = df[df[district_col].astype(str).str.contains(pattern, case=False, na=False, regex=True)].copy()
        print(f"  [note] {level}: no COUNTY column — matched '{district_col}' against known Milwaukee County districts instead")

    if filtered.empty:
        print(f"[warn] {level}: no rows matched Milwaukee County filter")
        return None

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"report_card_{level}_milwaukee.csv"
    filtered.to_csv(out_path, index=False)
    print(f"[ok] {level}: {len(filtered)} Milwaukee County rows saved to {out_path}")
    return out_path


def run_all():
    cfg = load_config()
    county_filter = cfg.get("county_filter", "Milwaukee")

    for level in ["school", "district"]:
        raw_path = fetch_level(level)
        filter_to_county(raw_path, level, county_filter)


if __name__ == "__main__":
    run_all()