"""
analyze_school_discipline_groups.py — Phase 2: student-group discipline
disparities (starting with Race/Ethnicity).

For every Milwaukee school it computes each racial group's out-of-school
suspension / expulsion experience using that group's OWN enrollment as the
denominator — so "Black students suspended at X per 100 Black students", the
defensible disparity measure, not a raw count.

Reuses the shared cleaning (district normalization, school-name aliases,
excluded consortiums, placeholder-school drop, suppression markers) so school
identities line up with the All-Students discipline build.

CRITICAL — suppression is much heavier at the group level (DPI redacts ~1/3 of
race rows). A suppressed group is a GAP, never a zero. A disparity ratio is a
LEAD, not a finding — verify before publishing.

Writes:
  data/processed/discipline_school_groups_trend.csv
    long: metric, dimension, group, category, unit, district, school, year,
          value, count, group_enrollment, status_flag
  data/processed/discipline_race_disparity.csv
    latest-year out-of-school-suspension disparity leaderboard, one row per
    school: each group's rate, the highest-vs-lowest ratio, the Black-vs-White
    ratio, and enrollment-share over/under-representation. Sorted by the widest
    gap — "which schools show the biggest disparity".

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

DIMENSION = "Race/Ethnicity"
# "Unknown" and "[Data Suppressed]" aren't real reportable groups.
DROP_GROUP_VALUES = {"Unknown", "[Data Suppressed]", "Unknown Race/Ethnicity"}
# A per-group rate needs a real base; below this the rate whipsaws on one or two
# incidents and shouldn't drive a disparity ranking.
MIN_GROUP_ENROLL = 20

CATEGORY_TYPES = {
    "suspension": {OSS_TYPE},
    "expulsion": EXPULSION_TYPES,
}
CATEGORY_UNIT = {"suspension": "rate", "expulsion": "count"}


def load_group_rows():
    df = pd.read_csv(RAW_DIR / "discipline_raw.csv", low_memory=False, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    mke = df[df["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)].copy()
    mke = mke[mke["GROUP_BY"] == DIMENSION]
    mke = mke[~mke["GROUP_BY_VALUE"].isin(DROP_GROUP_VALUES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]

    mke["removal_raw"] = mke["REMOVAL_COUNT"].astype(str).str.strip()
    mke["removal_num"] = pd.to_numeric(mke["REMOVAL_COUNT"], errors="coerce")
    mke["enroll_num"] = pd.to_numeric(mke["TFS_ENROLLMENT_COUNT"], errors="coerce")
    mke["district"] = mke["DISTRICT_NAME"].apply(normalize_district)
    mke["school"] = mke["SCHOOL_NAME"].astype(str).str.strip().replace(SCHOOL_NAME_ALIASES)
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
    # rate: needs a real denominator
    if pd.isna(enrollment) or enrollment < MIN_GROUP_ENROLL:
        return None, count, False, genuine_zero
    return round(RATE_PER * count / enrollment, 2), count, False, genuine_zero


def build_trend(mke):
    rows = []
    keys = ["district", "school", "SCHOOL_YEAR", "group"]
    for (district, school, year, group), g in mke.groupby(keys):
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
                "dimension": DIMENSION,
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
    """Latest-year out-of-school-suspension disparity, one row per school.

    Combines both measures the newsroom asked for: per-group rate + the
    highest/lowest and Black/White ratios, plus enrollment-share
    over-representation (group's share of suspensions vs share of enrollment).
    """
    latest = trend_df["year"].max()
    susp = trend_df[(trend_df["category"] == "suspension") & (trend_df["year"] == latest)]

    out = []
    for (district, school), g in susp.groupby(["district", "school"]):
        rated = g[g["value"].notna()]
        if len(rated) < 2:
            continue  # need at least two groups to have a disparity

        rates = dict(zip(rated["group"], rated["value"]))
        counts = dict(zip(rated["group"], rated["count"]))
        enrolls = dict(zip(rated["group"], rated["group_enrollment"]))

        top_group = max(rates, key=rates.get)
        bot_group = min(rates, key=rates.get)
        top_rate, bot_rate = rates[top_group], rates[bot_group]
        max_min_ratio = round(top_rate / bot_rate, 2) if bot_rate > 0 else None

        black_rate = rates.get("Black")
        white_rate = rates.get("White")
        bw_ratio = (round(black_rate / white_rate, 2)
                    if black_rate is not None and white_rate not in (None, 0) else None)

        # Enrollment-share over-representation across the groups we could rate.
        total_removals = sum(counts.values())
        total_enroll = sum(enrolls.values())
        shares = {}
        if total_removals > 0 and total_enroll > 0:
            for grp in rates:
                share_rem = counts[grp] / total_removals
                share_enr = enrolls[grp] / total_enroll
                shares[grp] = round(share_rem / share_enr, 2) if share_enr > 0 else None

        out.append({
            "district": district,
            "school": school,
            "year": latest,
            "groups_compared": len(rated),
            "highest_group": top_group,
            "highest_rate": top_rate,
            "lowest_group": bot_group,
            "lowest_rate": bot_rate,
            "high_low_ratio": max_min_ratio,
            "black_rate": black_rate,
            "white_rate": white_rate,
            "black_white_ratio": bw_ratio,
            "black_overrep_index": shares.get("Black"),
            "black_enrollment": None if enrolls.get("Black") is None else int(enrolls["Black"]),
        })

    df = pd.DataFrame(out)
    if df.empty:
        return df
    # "Which schools show the biggest disparity" — widest high/low gap first.
    return df.sort_values("high_low_ratio", ascending=False, na_position="last")


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
          f"(race x category x year), latest {latest}")
    print(f"     disparity leaderboard: {len(disparity_df)} schools with a rateable gap "
          f"-> {PROCESSED_DIR / 'discipline_race_disparity.csv'}")
    return trend_df, disparity_df


if __name__ == "__main__":
    run()
