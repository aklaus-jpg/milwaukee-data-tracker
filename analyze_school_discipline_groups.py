"""
analyze_school_discipline_groups.py — Phase 2: student-group discipline
disparities across several dimensions (race, gender, disability, English-learner
status, economic status).

For every Milwaukee school it computes each group's out-of-school suspension /
expulsion experience using that group's OWN enrollment as the denominator — so
"students with disabilities suspended at X per 100 students with disabilities",
the defensible disparity measure, not a raw count.

Reuses the shared cleaning (district normalization, school-name aliases,
excluded consortiums, placeholder-school drop, suppression markers) so school
identities line up with the All-Students discipline build.

CRITICAL — suppression is much heavier at the group level (DPI redacts a large
share of group rows). A suppressed group is a GAP, never a zero. A disparity
ratio is a LEAD, not a finding — verify before publishing.

Writes:
  data/processed/discipline_school_groups_trend.csv
    long: metric, dimension, group, category, unit, district, school, year,
          value, count, group_enrollment, status_flag
  data/processed/discipline_race_disparity.csv
    latest-year out-of-school-suspension disparity, one row per school PER
    DIMENSION: each dimension's focus-vs-reference ratio (Black/White, SwD/SwoD,
    EL/English-proficient, Econ-disadv/not, Male/Female), highest/lowest ratio,
    and enrollment-share over-representation. (Filename kept for continuity; it
    now covers all dimensions via a `dimension` column.)

Run directly:  python analyze_school_discipline_groups.py
"""
import pathlib

import pandas as pd

from analyze_school_discipline import (
    EXPULSION_TYPES,
    OSS_TYPE,
    RATE_PER,
    SUPPRESSION_MARKERS,
)
from analyze_school_enrollment import (
    COUNTY_FILTER,
    EXCLUDED_SCHOOL_NAMES,
    PLACEHOLDER_SCHOOL_NAMES,
    SCHOOL_NAME_ALIASES,
    normalize_district,
)

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")

# Each dimension names the "focus" group (the one over-represented in discipline
# nationally) and its "reference" group, for a clean headline ratio.
DIMENSIONS = {
    "Race/Ethnicity": {"focus": "Black", "reference": "White"},
    "Gender": {"focus": "Male", "reference": "Female"},
    "Disability Status": {"focus": "SwD", "reference": "SwoD"},
    "EL Status": {"focus": "EL", "reference": "Eng Prof"},
    "Economic Status": {"focus": "Econ Disadv", "reference": "Not Econ Disadv"},
}
# Non-informative group values (not real reportable groups).
DROP_GROUP_VALUES = {"Unknown", "[Data Suppressed]", "Unknown Race/Ethnicity"}
# A per-group rate needs a real base; below this the rate whipsaws on one or two
# incidents and shouldn't drive a disparity ranking.
MIN_GROUP_ENROLL = 20

CATEGORY_TYPES = {
    "all": {OSS_TYPE} | EXPULSION_TYPES,
    "suspension": {OSS_TYPE},
    "expulsion": EXPULSION_TYPES,
}
CATEGORY_UNIT = {"all": "rate", "suspension": "rate", "expulsion": "count"}


def load_group_rows():
    df = pd.read_csv(RAW_DIR / "discipline_raw.csv", low_memory=False, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    mke = df[df["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)].copy()

    # 2018-19 labeled the EL dimension "ELL Status" with value "ELL/LEP"; fold it
    # into the modern "EL Status" / "EL" so the trend is continuous.
    mke.loc[mke["GROUP_BY"] == "ELL Status", "GROUP_BY"] = "EL Status"
    mke["GROUP_BY_VALUE"] = mke["GROUP_BY_VALUE"].replace({"ELL/LEP": "EL"})

    mke = mke[mke["GROUP_BY"].isin(DIMENSIONS)]
    mke = mke[~mke["GROUP_BY_VALUE"].isin(DROP_GROUP_VALUES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]

    mke["removal_raw"] = mke["REMOVAL_COUNT"].astype(str).str.strip()
    mke["removal_num"] = pd.to_numeric(mke["REMOVAL_COUNT"], errors="coerce")
    mke["enroll_num"] = pd.to_numeric(mke["TFS_ENROLLMENT_COUNT"], errors="coerce")
    mke["district"] = mke["DISTRICT_NAME"].apply(normalize_district)
    mke["school"] = mke["SCHOOL_NAME"].astype(str).str.strip().replace(SCHOOL_NAME_ALIASES)
    mke["dimension"] = mke["GROUP_BY"]
    mke["group"] = mke["GROUP_BY_VALUE"].astype(str).str.strip()
    return mke


def group_year_value(rows_of_type, enrollment, unit):
    """One group's value for one category in one school-year.

    Returns (value_or_None, count_or_None, suppressed, genuine_zero).
    """
    suppressed = rows_of_type["removal_raw"].isin(SUPPRESSION_MARKERS).any()
    if suppressed:
        return None, None, True, False
    nums = rows_of_type["removal_num"].dropna()
    count = float(nums.sum()) if len(nums) else 0.0
    genuine_zero = count == 0
    if unit == "count":
        return count, count, False, genuine_zero
    if pd.isna(enrollment) or enrollment < MIN_GROUP_ENROLL:
        return None, count, False, genuine_zero
    return round(RATE_PER * count / enrollment, 2), count, False, genuine_zero


def build_trend(mke):
    rows = []
    keys = ["dimension", "district", "school", "SCHOOL_YEAR", "group"]
    for (dimension, district, school, year, group), g in mke.groupby(keys):
        enrollment = g["enroll_num"].max()
        for category, types in CATEGORY_TYPES.items():
            unit = CATEGORY_UNIT[category]
            sub = g[g["REMOVAL_TYPE_DESCRIPTION"].isin(types)]
            value, count, suppressed, zero = group_year_value(sub, enrollment, unit)

            notes = []
            if suppressed:
                notes.append("Suppressed (DPI privacy, count <10) — not zero")
            elif value is None and unit == "rate":
                notes.append(f"Group under {MIN_GROUP_ENROLL} students — rate omitted")
            elif zero:
                notes.append("Zero reported")

            rows.append({
                "metric": "discipline",
                "dimension": dimension,
                "group": group,
                "category": category,
                "unit": unit,
                "district": district,
                "school": school,
                "year": year,
                "value": value,
                "count": None if count is None else count,
                "group_enrollment": None if pd.isna(enrollment) else float(enrollment),
                "status_flag": "; ".join(notes),
            })
    return pd.DataFrame(rows)


def build_disparity(trend_df):
    """Latest-year out-of-school-suspension disparity, one row per school per
    dimension. Combines both measures: focus-vs-reference and highest-vs-lowest
    rate ratios, plus focus-group enrollment-share over-representation."""
    latest = trend_df["year"].max()
    susp = trend_df[(trend_df["category"] == "suspension") & (trend_df["year"] == latest)]

    out = []
    for (dimension, district, school), g in susp.groupby(["dimension", "district", "school"]):
        rated = g[g["value"].notna()]
        if len(rated) < 2:
            continue

        rates = dict(zip(rated["group"], rated["value"]))
        counts = dict(zip(rated["group"], rated["count"]))
        enrolls = dict(zip(rated["group"], rated["group_enrollment"]))

        top_group = max(rates, key=rates.get)
        bot_group = min(rates, key=rates.get)
        high_low = round(rates[top_group] / rates[bot_group], 2) if rates[bot_group] > 0 else None

        focus = DIMENSIONS[dimension]["focus"]
        reference = DIMENSIONS[dimension]["reference"]
        f_rate, r_rate = rates.get(focus), rates.get(reference)
        fr_ratio = (round(f_rate / r_rate, 2)
                    if f_rate is not None and r_rate not in (None, 0) else None)

        total_removals = sum(counts.values())
        total_enroll = sum(enrolls.values())
        overrep = None
        if focus in rates and total_removals > 0 and total_enroll > 0 and enrolls[focus] > 0:
            overrep = round((counts[focus] / total_removals) / (enrolls[focus] / total_enroll), 2)

        out.append({
            "dimension": dimension,
            "district": district,
            "school": school,
            "year": latest,
            "focus_group": focus,
            "focus_rate": f_rate,
            "reference_group": reference,
            "reference_rate": r_rate,
            "focus_reference_ratio": fr_ratio,
            "highest_group": top_group,
            "highest_rate": rates[top_group],
            "lowest_group": bot_group,
            "lowest_rate": rates[bot_group],
            "high_low_ratio": high_low,
            "focus_overrep_index": overrep,
            "focus_enrollment": None if enrolls.get(focus) is None else int(enrolls[focus]),
        })

    df = pd.DataFrame(out)
    if df.empty:
        return df
    return df.sort_values(
        ["dimension", "focus_reference_ratio"], ascending=[True, False], na_position="last"
    )


def run():
    mke = load_group_rows()
    trend_df = build_trend(mke)
    disparity_df = build_disparity(trend_df)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    trend_df.to_csv(PROCESSED_DIR / "discipline_school_groups_trend.csv", index=False)
    disparity_df.to_csv(PROCESSED_DIR / "discipline_race_disparity.csv", index=False)

    n_schools = trend_df.drop_duplicates(["district", "school"]).shape[0]
    latest = trend_df["year"].max()
    print(f"[ok] discipline_school_groups: {n_schools} schools, {len(trend_df)} rows "
          f"({len(DIMENSIONS)} dimensions x category x year), latest {latest}")
    if not disparity_df.empty:
        by_dim = disparity_df.groupby("dimension").size().to_dict()
        print(f"     disparity rows per dimension: {by_dim}")
    return trend_df, disparity_df


if __name__ == "__main__":
    run()
