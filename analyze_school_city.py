"""
analyze_school_city.py — tags each Milwaukee County school with its physical
CITY, so the dashboard can default to the city of Milwaukee (vs the whole
county, which includes Wauwatosa, West Allis, Shorewood, etc.).

DPI's WISEdash data files have no city field. DPI's separate GIS "Public
Schools, Wisconsin" point layer does (CITY, address, lat/long). We join it to
our schools by DISTRICT_CODE + SCHOOL_CODE (stable, name-independent) and emit a
lookup keyed by the SAME normalized district + aliased school name the rest of
the pipeline uses.

Writes: data/processed/school_city.csv
  columns: district, school, city, is_city  (is_city = physical city == Milwaukee)

Run directly:  python analyze_school_city.py
"""
import io
import pathlib

import pandas as pd
import requests

from analyze_school_enrollment import (
    COUNTY_FILTER,
    EXCLUDED_SCHOOL_NAMES,
    PLACEHOLDER_SCHOOL_NAMES,
    SCHOOL_NAME_ALIASES,
    normalize_district,
)

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")

GIS_URL = (
    "https://opendata.arcgis.com/api/v3/datasets/"
    "d383fe81275e46f2a5a5c4f1a0c2eb85_0/downloads/data?format=csv&spatialRefId=4326"
)


def code_key(district_code, school_code):
    """Normalize a (district, school) code pair to a stable zero-padded key.
    Raw WISEdash stores school codes as floats ('12.0'); GIS uses '0012'."""
    try:
        d = str(int(float(district_code))).zfill(4)
        s = str(int(float(school_code))).zfill(4)
        return f"{d}-{s}"
    except (ValueError, TypeError):
        return None


def load_gis_city_map():
    r = requests.get(GIS_URL, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    g = pd.read_csv(io.BytesIO(r.content), low_memory=False, dtype=str)
    g.columns = [c.strip() for c in g.columns]
    gm = g[g["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)]
    city_by_key = {}
    for _, r in gm.iterrows():
        k = code_key(r.get("SDID"), r.get("SCH_CODE"))
        if k and pd.notna(r.get("CITY")):
            city_by_key[k] = str(r["CITY"]).strip()
    return city_by_key


def run():
    city_by_key = load_gis_city_map()

    e = pd.read_csv(RAW_DIR / "enrollment_raw.csv", low_memory=False, dtype=str)
    e.columns = [c.strip() for c in e.columns]
    mke = e[e["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)].copy()
    mke = mke[~mke["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]
    mke["district"] = mke["DISTRICT_NAME"].apply(normalize_district)
    mke["school"] = mke["SCHOOL_NAME"].astype(str).str.strip().replace(SCHOOL_NAME_ALIASES)
    mke["key"] = [code_key(d, s) for d, s in zip(mke["DISTRICT_CODE"], mke["SCHOOL_CODE"])]
    mke["city"] = mke["key"].map(city_by_key)

    # One row per (district, school): take the most common city seen for it.
    rows = []
    for (district, school), g in mke.groupby(["district", "school"]):
        cities = g["city"].dropna()
        city = cities.mode().iloc[0] if not cities.empty else ""
        rows.append({
            "district": district,
            "school": school,
            "city": city,
            "is_city": bool(city and city.title() == "Milwaukee"),
        })
    out = pd.DataFrame(rows)

    # A few schools have no GIS match (closed schools, code drift). Infer their
    # city from the district's majority among matched schools — e.g. an MPS
    # school with no location is still city, a Saint Francis one is not. This
    # correctly keeps suburban unmatched schools out of the city view.
    known = out[out["city"] != ""]
    district_is_city = known.groupby("district")["is_city"].mean()
    unmatched = out["city"] == ""
    out.loc[unmatched, "is_city"] = out.loc[unmatched, "district"].map(
        lambda d: district_is_city.get(d, 1.0) >= 0.5)
    out.loc[unmatched, "city"] = out.loc[unmatched, "is_city"].map(
        lambda v: "Milwaukee (inferred)" if v else "suburban (inferred)")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(PROCESSED_DIR / "school_city.csv", index=False)

    n = len(out)
    n_city = int(out["is_city"].sum())
    n_unknown = int((out["city"] == "").sum())
    print(f"[ok] school_city: {n} schools — {n_city} in the city of Milwaukee, "
          f"{n - n_city - n_unknown} suburban, {n_unknown} unmatched (no GIS location) "
          f"-> {PROCESSED_DIR / 'school_city.csv'}")
    return out


if __name__ == "__main__":
    run()
