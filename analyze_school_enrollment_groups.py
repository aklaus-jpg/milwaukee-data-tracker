"""
analyze_school_enrollment_groups.py — Phase 3: enrollment by student group.

Unlike discipline/absenteeism, enrollment by group is demographic COMPOSITION
(what share of a school each group is), not a rate disparity — so this produces
per-group headcounts and each group's share of the school, for a composition
panel and a group-enrollment trend. No focus/reference ratio.

Dimensions: race, gender, disability, English-learner status, economic status.
Reuses shared cleaning + the same DIMENSIONS group list. Suppressed groups are
gaps, never zeros.

Writes: data/processed/enrollment_school_groups_trend.csv
  long: metric, dimension, group, district, school, year, value, share,
        status_flag   (value = group headcount; share = fraction of the school)

Run directly:  python analyze_school_enrollment_groups.py
"""
import pathlib

import pandas as pd

from analyze_school_discipline import SUPPRESSION_MARKERS
from analyze_school_discipline_groups import DIMENSIONS, DROP_GROUP_VALUES
from analyze_school_enrollment import (
    COUNTY_FILTER,
    EXCLUDED_SCHOOL_NAMES,
    PLACEHOLDER_SCHOOL_NAMES,
    SCHOOL_NAME_ALIASES,
    normalize_district,
)

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")


def load_rows():
    df = pd.read_csv(RAW_DIR / "enrollment_raw.csv", low_memory=False, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    mke = df[df["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)].copy()
    mke.loc[mke["GROUP_BY"] == "ELL Status", "GROUP_BY"] = "EL Status"
    mke["GROUP_BY_VALUE"] = mke["GROUP_BY_VALUE"].replace({"ELL/LEP": "EL"})
    mke = mke[~mke["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]
    mke["cnt_raw"] = mke["STUDENT_COUNT"].astype(str).str.strip()
    mke["cnt_num"] = pd.to_numeric(mke["STUDENT_COUNT"], errors="coerce")
    mke["district"] = mke["DISTRICT_NAME"].apply(normalize_district)
    mke["school"] = mke["SCHOOL_NAME"].astype(str).str.strip().replace(SCHOOL_NAME_ALIASES)
    return mke


def build_trend(mke):
    # School-year totals (All Students) for the share denominator.
    allstu = mke[(mke["GROUP_BY"] == "All Students") & (mke["GROUP_BY_VALUE"] == "All Students")]
    totals = allstu.groupby(["district", "school", "SCHOOL_YEAR"])["cnt_num"].max()

    groups = mke[mke["GROUP_BY"].isin(DIMENSIONS) & ~mke["GROUP_BY_VALUE"].isin(DROP_GROUP_VALUES)].copy()
    groups["dimension"] = groups["GROUP_BY"]
    groups["group"] = groups["GROUP_BY_VALUE"].astype(str).str.strip()

    rows = []
    for (dimension, district, school, year, group), g in groups.groupby(
            ["dimension", "district", "school", "SCHOOL_YEAR", "group"]):
        r = g.iloc[0]
        total = totals.get((district, school, year))
        suppressed = str(r["cnt_raw"]) in SUPPRESSION_MARKERS

        notes = []
        value = share = None
        if suppressed:
            notes.append("Suppressed (DPI privacy) — not zero")
        elif not pd.isna(r["cnt_num"]):
            value = float(r["cnt_num"])
            if total and total > 0:
                share = round(value / total, 4)

        rows.append({
            "metric": "enrollment",
            "dimension": dimension,
            "group": group,
            "district": district,
            "school": school,
            "year": year,
            "value": value,
            "share": share,
            "status_flag": "; ".join(notes),
        })
    return pd.DataFrame(rows)


def run():
    mke = load_rows()
    trend_df = build_trend(mke)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PROCESSED_DIR / "enrollment_school_groups_trend.csv"
    trend_df.to_csv(out, index=False)
    n_schools = trend_df.drop_duplicates(["district", "school"]).shape[0]
    print(f"[ok] enrollment_school_groups: {n_schools} schools, {len(trend_df)} rows, "
          f"latest {trend_df['year'].max()} -> {out}")
    return trend_df


if __name__ == "__main__":
    run()
