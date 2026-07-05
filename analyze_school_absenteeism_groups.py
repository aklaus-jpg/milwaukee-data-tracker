"""
analyze_school_absenteeism_groups.py — Phase 3: student-group CHRONIC
ABSENTEEISM disparities, mirroring the discipline group build.

DPI reports ABSENCE_RATE (percent chronically absent) per group directly, so no
denominator math — the disparity is simply one group's rate vs another's.
Dimensions: race, gender, disability, English-learner status, economic status.

Reuses shared cleaning + the same DIMENSIONS/focus-reference definitions as the
discipline group build. Suppressed groups (DPI redacts ~1/3 of group rows) are
gaps, never zeros; groups under 20 students are omitted (noisy rate). A gap is a
LEAD, not a finding.

Writes:
  data/processed/absenteeism_school_groups_trend.csv
    long: metric, dimension, group, district, school, year, value, count,
          status_flag   (value = % chronically absent)
  data/processed/absenteeism_disparity.csv
    latest-year disparity, one row per school per dimension (focus-vs-reference
    and highest-vs-lowest rate ratios).

Run directly:  python analyze_school_absenteeism_groups.py
"""
import pathlib

import pandas as pd

from analyze_school_discipline import SUPPRESSION_MARKERS
from analyze_school_discipline_groups import DIMENSIONS, DROP_GROUP_VALUES, MIN_GROUP_ENROLL
from analyze_school_enrollment import (
    COUNTY_FILTER,
    EXCLUDED_SCHOOL_NAMES,
    PLACEHOLDER_SCHOOL_NAMES,
    SCHOOL_NAME_ALIASES,
    normalize_district,
)

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")


def load_group_rows():
    df = pd.read_csv(RAW_DIR / "chronic_absenteeism_raw.csv", low_memory=False, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    mke = df[df["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)].copy()
    mke.loc[mke["GROUP_BY"] == "ELL Status", "GROUP_BY"] = "EL Status"
    mke["GROUP_BY_VALUE"] = mke["GROUP_BY_VALUE"].replace({"ELL/LEP": "EL"})

    mke = mke[mke["GROUP_BY"].isin(DIMENSIONS)]
    mke = mke[~mke["GROUP_BY_VALUE"].isin(DROP_GROUP_VALUES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]

    mke["rate_raw"] = mke["ABSENCE_RATE"].astype(str).str.strip()
    mke["rate_num"] = pd.to_numeric(mke["ABSENCE_RATE"], errors="coerce")
    mke["count_num"] = pd.to_numeric(mke["STUDENT_COUNT"], errors="coerce")
    mke["district"] = mke["DISTRICT_NAME"].apply(normalize_district)
    mke["school"] = mke["SCHOOL_NAME"].astype(str).str.strip().replace(SCHOOL_NAME_ALIASES)
    mke["dimension"] = mke["GROUP_BY"]
    mke["group"] = mke["GROUP_BY_VALUE"].astype(str).str.strip()
    return mke


def build_trend(mke):
    rows = []
    for (dimension, district, school, year, group), g in mke.groupby(
            ["dimension", "district", "school", "SCHOOL_YEAR", "group"]):
        r = g.iloc[0]
        count = r["count_num"]
        suppressed = str(r["rate_raw"]) in SUPPRESSION_MARKERS or (
            pd.isna(r["rate_num"]) and r["rate_raw"] not in ("", "nan"))

        notes = []
        value = None
        if suppressed:
            notes.append("Suppressed (DPI privacy) — not zero")
        elif pd.isna(count) or count < MIN_GROUP_ENROLL:
            notes.append(f"Group under {MIN_GROUP_ENROLL} students — rate omitted")
        else:
            value = round(float(r["rate_num"]), 1)

        rows.append({
            "metric": "chronic_absenteeism",
            "dimension": dimension,
            "group": group,
            "district": district,
            "school": school,
            "year": year,
            "value": value,
            "count": None if pd.isna(count) else float(count),
            "status_flag": "; ".join(notes),
        })
    return pd.DataFrame(rows)


def build_disparity(trend_df):
    latest = trend_df["year"].max()
    yr = trend_df[trend_df["year"] == latest]

    out = []
    for (dimension, district, school), g in yr.groupby(["dimension", "district", "school"]):
        rated = g[g["value"].notna()]
        if len(rated) < 2:
            continue
        rates = dict(zip(rated["group"], rated["value"]))

        top_group = max(rates, key=rates.get)
        bot_group = min(rates, key=rates.get)
        high_low = round(rates[top_group] / rates[bot_group], 2) if rates[bot_group] > 0 else None

        focus = DIMENSIONS[dimension]["focus"]
        reference = DIMENSIONS[dimension]["reference"]
        f_rate, r_rate = rates.get(focus), rates.get(reference)
        fr_ratio = (round(f_rate / r_rate, 2)
                    if f_rate is not None and r_rate not in (None, 0) else None)

        out.append({
            "dimension": dimension, "district": district, "school": school, "year": latest,
            "focus_group": focus, "focus_rate": f_rate,
            "reference_group": reference, "reference_rate": r_rate,
            "focus_reference_ratio": fr_ratio,
            "highest_group": top_group, "highest_rate": rates[top_group],
            "lowest_group": bot_group, "lowest_rate": rates[bot_group],
            "high_low_ratio": high_low,
        })

    df = pd.DataFrame(out)
    if df.empty:
        return df
    return df.sort_values(
        ["dimension", "focus_reference_ratio"], ascending=[True, False], na_position="last")


def build_all_students_trend():
    """The school's overall (All Students) chronic-absenteeism trend, so the
    absenteeism tab gets a school drill-down like enrollment/discipline."""
    df = pd.read_csv(RAW_DIR / "chronic_absenteeism_raw.csv", low_memory=False, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    mke = df[df["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)].copy()
    mke = mke[(mke["GROUP_BY"] == "All Students") & (mke["GROUP_BY_VALUE"] == "All Students")]
    mke = mke[~mke["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]
    mke["district"] = mke["DISTRICT_NAME"].apply(normalize_district)
    mke["school"] = mke["SCHOOL_NAME"].astype(str).str.strip().replace(SCHOOL_NAME_ALIASES)
    mke["value"] = pd.to_numeric(mke["ABSENCE_RATE"], errors="coerce")
    mke["enroll"] = pd.to_numeric(mke["STUDENT_COUNT"], errors="coerce")

    rows = []
    for (district, school), g in mke.groupby(["district", "school"]):
        s = g.groupby("SCHOOL_YEAR")["value"].first().sort_index()
        enr = g.groupby("SCHOOL_YEAR")["enroll"].max()
        yoy = s.diff()
        pct = (s.pct_change() * 100).replace([float("inf"), float("-inf")], pd.NA)
        for yr, v in s.items():
            rows.append({
                "metric": "chronic_absenteeism", "district": district, "school": school,
                "group": f"{school} — All Students", "year": yr,
                "value": None if pd.isna(v) else round(float(v), 1),
                "yoy_change": None if pd.isna(yoy.get(yr)) else round(float(yoy.get(yr)), 1),
                "pct_change": None if pd.isna(pct.get(yr)) else round(float(pct.get(yr)), 1),
                "status_flag": "",
                "enrollment": None if pd.isna(enr.get(yr)) else float(enr.get(yr)),
            })
    return pd.DataFrame(rows)


def run():
    mke = load_group_rows()
    trend_df = build_trend(mke)
    disparity_df = build_disparity(trend_df)
    all_students_df = build_all_students_trend()

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    trend_df.to_csv(PROCESSED_DIR / "absenteeism_school_groups_trend.csv", index=False)
    disparity_df.to_csv(PROCESSED_DIR / "absenteeism_disparity.csv", index=False)
    # File prefix matches the dashboard metric key (chronic_absenteeism) so the
    # movers builder and SCHOOL_TREND_FILES find it by convention.
    all_students_df.to_csv(PROCESSED_DIR / "chronic_absenteeism_school_trend.csv", index=False)

    n_schools = trend_df.drop_duplicates(["district", "school"]).shape[0]
    latest = trend_df["year"].max()
    print(f"[ok] absenteeism_school_groups: {n_schools} schools, {len(trend_df)} rows, latest {latest}")
    if not disparity_df.empty:
        print(f"     disparity rows per dimension: {disparity_df.groupby('dimension').size().to_dict()}")
    return trend_df, disparity_df


if __name__ == "__main__":
    run()
