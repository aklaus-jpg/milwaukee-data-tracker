"""
analyze_school_incidents.py — the REASON side of discipline: school-level
incident counts broken out by BEHAVIOR_TYPE (why students were disciplined),
from DPI's separate discipline_incidents dataset.

Behavior categories: Assault, Drugs and Alcohol, Endangering Behavior, Weapon
Related, Other Violation of School Rules. Distinct from the removals
(suspension/expulsion) file — that's the consequence, this is the reason.

Reuses the shared cleaning (normalization, aliases, exclusions, suppression
markers) so schools line up with the other discipline builds. Suppressed
behaviors are gaps, never zeros.

Writes: data/processed/discipline_incidents_school.csv
  long: metric, district, school, year, behavior, count, rate, share, enrollment,
        status_flag   (behavior "All incidents" = the school-year total)
  rate  = incidents per 100 enrolled students
  share = this behavior's fraction of the school-year's incidents

Run directly:  python analyze_school_incidents.py
"""
import pathlib

import pandas as pd

from analyze_school_discipline import RATE_PER, SUPPRESSION_MARKERS
from analyze_school_enrollment import (
    COUNTY_FILTER,
    EXCLUDED_SCHOOL_NAMES,
    PLACEHOLDER_SCHOOL_NAMES,
    SCHOOL_NAME_ALIASES,
    normalize_district,
)

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")

BEHAVIORS = [
    "Assault",
    "Drugs and Alcohol",
    "Endangering Behavior",
    "Weapon Related",
    "Other Violation of School Rules",
]


def load_school_rows():
    df = pd.read_csv(RAW_DIR / "discipline_incidents_raw.csv", low_memory=False, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    mke = df[df["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)].copy()
    mke = mke[(mke["GROUP_BY"] == "All Students") & (mke["GROUP_BY_VALUE"] == "All Students")]
    mke = mke[~mke["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]

    mke["inc_raw"] = mke["INCIDENTS_COUNT"].astype(str).str.strip()
    mke["inc_num"] = pd.to_numeric(mke["INCIDENTS_COUNT"], errors="coerce")
    mke["enroll_num"] = pd.to_numeric(mke["TFS_ENROLLMENT_COUNT"], errors="coerce")
    mke["district"] = mke["DISTRICT_NAME"].apply(normalize_district)
    mke["school"] = mke["SCHOOL_NAME"].astype(str).str.strip().replace(SCHOOL_NAME_ALIASES)
    return mke


def behavior_count(rows):
    """(count_or_None, suppressed) for one behavior in one school-year."""
    if rows["inc_raw"].isin(SUPPRESSION_MARKERS).any():
        return None, True
    nums = rows["inc_num"].dropna()
    return (float(nums.sum()) if len(nums) else 0.0), False


def build(mke):
    rows = []
    for (district, school, year), g in mke.groupby(["district", "school", "SCHOOL_YEAR"]):
        enrollment = g["enroll_num"].max()
        per_behavior = {}
        any_suppressed = False
        for b in BEHAVIORS:
            count, suppressed = behavior_count(g[g["BEHAVIOR_TYPE"] == b])
            per_behavior[b] = count
            any_suppressed = any_suppressed or suppressed

        known = [c for c in per_behavior.values() if c is not None]
        total = sum(known) if known else 0.0

        def emit(behavior, count, is_total=False):
            notes = []
            if count is None:
                notes.append("Suppressed (DPI privacy) — not zero")
            elif is_total and any_suppressed:
                notes.append("Total excludes suppressed behavior(s) — undercount")
            elif count == 0 and not is_total:
                notes.append("None reported")
            rate = (round(RATE_PER * count / enrollment, 2)
                    if count is not None and pd.notna(enrollment) and enrollment > 0 else None)
            share = (round(count / total, 4)
                     if count is not None and total > 0 and not is_total else None)
            rows.append({
                "metric": "discipline_incidents",
                "district": district,
                "school": school,
                "year": year,
                "behavior": behavior,
                "count": None if count is None else count,
                "rate": rate,
                "share": share,
                "enrollment": None if pd.isna(enrollment) else float(enrollment),
                "status_flag": "; ".join(notes),
            })

        emit("All incidents", total, is_total=True)
        for b in BEHAVIORS:
            emit(b, per_behavior[b])
    return pd.DataFrame(rows)


def run():
    mke = load_school_rows()
    df = build(mke)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PROCESSED_DIR / "discipline_incidents_school.csv"
    df.to_csv(out, index=False)

    n_schools = df.drop_duplicates(["district", "school"]).shape[0]
    latest = df["year"].max()
    print(f"[ok] discipline_incidents_school: {n_schools} schools, {len(df)} rows "
          f"(behavior x school x year), latest {latest} -> {out}")
    return df


if __name__ == "__main__":
    run()
