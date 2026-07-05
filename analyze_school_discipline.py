"""
analyze_school_discipline.py — builds school-level discipline trends (out-of-school
suspensions + expulsions, expressed as a RATE) for Milwaukee County schools.

Reuses the SAME cleaning/normalization/stitching logic as the enrollment
school-level build (imported from analyze_school_enrollment — never rewritten):
  - normalize_district()        (Inc/Inc./, Inc. collapse + overrides)
  - SCHOOL_NAME_ALIASES         (Carmen South rename, etc.)
  - EXCLUDED_SCHOOL_NAMES       (Between the Lakes, Kiel eSchool consortiums)
  - PLACEHOLDER_SCHOOL_NAMES    ([Districtwide]/[Statewide]/[All])
  - SMALL_COUNT_THRESHOLD       (DPI <10 privacy convention)
  - find_stitchable_schools()   (continuous line across authorizer changes)

DEFINITION (matches the district-level discipline file's removal-type coverage):
  This DPI file contains exactly three removal types — "Out of School
  Suspension", "Expulsion with Services Offered", "Expulsion without Services
  Offered" — plus blank-removal-type rows that mean "this school reported zero
  removals" (REMOVAL_COUNT == 0). There is NO in-school-suspension category in
  this file at all, so summing suspensions+expulsions == the district file's
  total. We divide that sum by TFS_ENROLLMENT_COUNT to get a comparable RATE
  (raw counts alone can't compare a big school to a small one).

  value = removals per 100 enrolled students
        = 100 * (OSS + expulsions) / TFS_ENROLLMENT_COUNT
  NOTE: this is a REMOVAL rate, not "% of students suspended" — one student can
  be counted multiple times, so the rate can exceed 100.

SUPPRESSION — the critical part (see discipline_spec.md):
  DPI suppresses small counts. A suppressed suspension count MUST NOT render as
  0 (that would be a false exoneration of the school). If any suspension/
  expulsion component of a school-year is suppressed ("*" / "[Data Suppressed]"),
  that year gets value = BLANK (a chart GAP, never a plotted point) and the
  status_flag "Suppressed (DPI privacy, count <10) — not zero".

  At the Milwaukee school x All-Students level the CURRENT DPI file happens to
  carry no "*" suppression (suppression only hits demographic subgroups, which
  we don't use, and small counts 1-9 are published as real numbers). This code
  still implements the suppression->gap path as a mandatory safety net because
  DPI's conventions drift year to year — if suppression ever appears at this
  level, it fails safe (gap) rather than silently zeroing.

  A genuine zero (explicit blank-removal-type row, count 0, real enrollment) is
  a TRUE zero and IS plotted as 0.0 — it is not suppression.

Writes:
  data/processed/discipline_school_trend.csv
    columns: metric, district, school, group, year, value, yoy_change,
             pct_change, status_flag
  data/processed/discipline_school_flags.csv
    reporting tip sheet — schools with the biggest year-over-year rate INCREASES

Run directly:  python analyze_school_discipline.py
"""
import pathlib

import pandas as pd

from analyze_school_enrollment import (
    COUNTY_FILTER,
    EXCLUDED_SCHOOL_NAMES,
    PLACEHOLDER_SCHOOL_NAMES,
    SCHOOL_NAME_ALIASES,
    SMALL_COUNT_THRESHOLD,
    find_stitchable_schools,
    normalize_district,
)

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")

# The only three removal categories in this DPI file. Summing these == the
# district-level file's total (it filters nothing; blank-type rows add 0).
SUSP_EXP_TYPES = {
    "Out of School Suspension",
    "Expulsion with Services Offered",
    "Expulsion without Services Offered",
}
OSS_TYPE = "Out of School Suspension"
EXPULSION_TYPES = {"Expulsion with Services Offered", "Expulsion without Services Offered"}

# Each school-year is emitted three ways (long format, one row per category).
# Suspensions and the combined total are per-100 rates; expulsions are rare
# enough that a raw count reads truer than a jittery sub-1 rate.
CATEGORY_UNIT = {"all": "rate", "suspension": "rate", "expulsion": "count"}
ZERO_NOTE = {
    "all": "Zero suspensions/expulsions reported",
    "suspension": "Zero out-of-school suspensions reported",
    "expulsion": "Zero expulsions reported",
}
# How DPI marks a redacted count. Kept as a set so drift (e.g. a new literal) is
# a one-line change.
SUPPRESSION_MARKERS = {"*", "[Data Suppressed]", "[Suppressed]"}

RATE_PER = 100  # value is removals per 100 enrolled students

# Reporting tip sheet: ignore tiny-enrollment schools (their rates swing wildly
# on one incident) and only surface reliable year-over-year jumps.
MIN_ENROLL_FOR_FLAG = 50
TOP_N_FLAGS = 25


def load_school_rows():
    raw_path = RAW_DIR / "discipline_raw.csv"
    df = pd.read_csv(raw_path, low_memory=False, dtype=str)
    df.columns = [c.strip() for c in df.columns]

    mke = df[df["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)].copy()

    # "All Students" total rows only — exact match on BOTH columns to exclude
    # demographic subgroups (Race/Ethnicity, Disability, Gender, ...), which
    # would multiply and inflate the counts. NOT .contains.
    mke = mke[(mke["GROUP_BY"] == "All Students") & (mke["GROUP_BY_VALUE"] == "All Students")]

    mke = mke[~mke["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]

    # Keep REMOVAL_COUNT as raw string so we can distinguish a suppression marker
    # ("*") from a real 0 before any numeric coercion.
    mke["removal_raw"] = mke["REMOVAL_COUNT"].astype(str).str.strip()
    mke["removal_num"] = pd.to_numeric(mke["REMOVAL_COUNT"], errors="coerce")
    mke["enroll_num"] = pd.to_numeric(mke["TFS_ENROLLMENT_COUNT"], errors="coerce")

    mke["district"] = mke["DISTRICT_NAME"].apply(normalize_district)
    school_names = mke["SCHOOL_NAME"].astype(str).str.strip()
    mke["school"] = school_names.replace(SCHOOL_NAME_ALIASES)
    return mke


def _category_count(rows_of_type, reported):
    """Reduce one removal category's rows for a school-year to a count.

    Returns (count_or_None, suppressed, genuine_zero). count is None for a gap
    (suppressed, or the school didn't report at all this year); 0.0 is a real
    reported zero.
    """
    suppressed = rows_of_type["removal_raw"].isin(SUPPRESSION_MARKERS).any()
    if suppressed:
        return None, True, False  # fail safe: suppressed is NOT zero
    nums = rows_of_type["removal_num"].dropna()
    if len(nums) > 0:
        return float(nums.sum()), False, False
    if reported:
        return 0.0, False, True  # school reported, just none of this type
    return None, False, False


def aggregate_school_year(d):
    """Collapse one school-year's rows into per-category counts.

    Returns {enrollment, enroll_missing, cats: {category: {count, suppressed,
    genuine_zero}}} for categories all / suspension / expulsion.
    """
    oss = d[d["REMOVAL_TYPE_DESCRIPTION"] == OSS_TYPE]
    exp = d[d["REMOVAL_TYPE_DESCRIPTION"].isin(EXPULSION_TYPES)]

    enrollment = d["enroll_num"].max()
    enroll_missing = pd.isna(enrollment) or enrollment <= 0

    # A blank-removal-type row with count 0 is DPI's explicit "zero removals".
    has_zero_row = ((d["REMOVAL_TYPE_DESCRIPTION"].isna()) & (d["removal_num"] == 0)).any()
    reported = (len(oss) > 0) or (len(exp) > 0) or has_zero_row

    s_count, s_sup, s_zero = _category_count(oss, reported)
    e_count, e_sup, e_zero = _category_count(exp, reported)

    # "all" = suspensions + expulsions. Suppressed if either component is; a gap
    # if either component is unknown (can't sum an unknown into a total).
    if s_sup or e_sup:
        a_count, a_sup, a_zero = None, True, False
    elif s_count is None or e_count is None:
        a_count, a_sup, a_zero = None, False, False
    else:
        a_count = s_count + e_count
        a_count, a_sup, a_zero = a_count, False, (a_count == 0)

    return {
        "enrollment": None if pd.isna(enrollment) else float(enrollment),
        "enroll_missing": bool(enroll_missing),
        "cats": {
            "all": {"count": a_count, "suppressed": a_sup, "genuine_zero": a_zero},
            "suspension": {"count": s_count, "suppressed": s_sup, "genuine_zero": s_zero},
            "expulsion": {"count": e_count, "suppressed": e_sup, "genuine_zero": e_zero},
        },
    }


def _cat_value(agg, category, unit):
    """The plotted value for a category: a per-100 rate, or a raw count."""
    cat = agg["cats"][category]
    if cat["suppressed"] or cat["count"] is None:
        return None
    if unit == "rate":
        if agg["enroll_missing"]:
            return None
        return round(RATE_PER * cat["count"] / agg["enrollment"], 2)
    return float(cat["count"])


def build_school_trend_rows(school, g_df, latest_year, stitched):
    years = sorted(g_df["SCHOOL_YEAR"].unique())
    district_by_year = g_df.groupby("SCHOOL_YEAR")["district"].first()
    per_year = {yr: aggregate_school_year(g_df[g_df["SCHOOL_YEAR"] == yr]) for yr in years}

    last_year = max(years)
    is_closed_out = last_year < latest_year
    current_district = district_by_year.loc[last_year]

    rows = []
    for category, unit in CATEGORY_UNIT.items():
        series = pd.Series(
            {yr: _cat_value(per_year[yr], category, unit) for yr in years}, dtype="float64"
        ).sort_index()
        yoy = series.diff()
        pct = (series.pct_change() * 100).replace([float("inf"), float("-inf")], pd.NA)

        prev_district = None
        for yr in years:
            agg = per_year[yr]
            cat = agg["cats"][category]
            count = cat["count"]
            val = series.get(yr)
            this_district = district_by_year.loc[yr]

            notes = []
            if stitched and prev_district is not None and this_district != prev_district:
                notes.append(f"Authorizer/reporting changed to {this_district}, {yr}")
            prev_district = this_district

            # Small counts make a RATE imprecise; a COUNT of "3 expulsions" is
            # exact, so that flag only applies to rate categories.
            small = unit == "rate" and count is not None and 0 < count < SMALL_COUNT_THRESHOLD
            if cat["suppressed"]:
                notes.append("Suppressed (DPI privacy, count <10) — not zero")
            elif unit == "rate" and agg["enroll_missing"] and count is not None:
                notes.append("Enrollment unavailable — rate not computable")
            elif count is None:
                notes.append("No discipline data reported")
            elif small:
                notes.append("Small count (<10 removals) — rate imprecise")
            elif cat["genuine_zero"]:
                notes.append(ZERO_NOTE[category])

            if is_closed_out:
                notes.append(f"No data after {last_year}")

            if unit == "rate":
                reliable = not (cat["suppressed"] or small
                                or (agg["enroll_missing"] and count is not None) or val is None)
            else:
                reliable = not (cat["suppressed"] or val is None)

            rows.append({
                "metric": "discipline",
                "category": category,
                "unit": unit,
                "district": current_district if stitched else this_district,
                "school": school,
                "group": f"{school} — All Students",
                "year": yr,
                "value": None if val is None or pd.isna(val) else val,
                "yoy_change": None if pd.isna(yoy.get(yr)) else yoy.get(yr),
                "pct_change": None if pd.isna(pct.get(yr)) else float(pct.get(yr)),
                "status_flag": "; ".join(notes),
                "enrollment": agg["enrollment"],
                # internal-only (dropped before write), used by flags/movers
                "_count": count,
                "_reliable": reliable,
            })
    return rows, is_closed_out


def build_trend(mke):
    latest_year = mke["SCHOOL_YEAR"].max()
    stitchable = find_stitchable_schools(mke)
    stitched_mask = mke["school"].isin(stitchable)

    trend_rows = []
    closure_count = 0

    for school, g_df in mke[stitched_mask].groupby("school"):
        rows, _ = build_school_trend_rows(school, g_df, latest_year, stitched=True)
        trend_rows.extend(rows)
        if rows and any(f"No data after" in r["status_flag"] for r in rows):
            closure_count += 1

    for (district, school), g_df in mke[~stitched_mask].groupby(["district", "school"]):
        rows, is_closed_out = build_school_trend_rows(school, g_df, latest_year, stitched=False)
        trend_rows.extend(rows)
        if is_closed_out:
            closure_count += 1

    return pd.DataFrame(trend_rows), latest_year, closure_count


def build_flags(trend_df):
    """Reporting tip sheet: schools with the biggest year-over-year rate
    INCREASES. A spike is a LEAD, not a fact — verify before publishing.
    Gated to reliable, non-tiny school-years so a 2->8 swing on a 30-student
    school doesn't drown out real trends.
    """
    # Tip sheet stays the combined suspension+expulsion rate (category "all").
    df = trend_df[trend_df["category"] == "all"].copy()
    df = df[
        df["_reliable"]
        & df["yoy_change"].notna()
        & (df["yoy_change"] > 0)
        & (df["enrollment"] >= MIN_ENROLL_FOR_FLAG)
    ]
    df = df.sort_values("yoy_change", ascending=False).head(TOP_N_FLAGS)

    # Nearly every 2021-22 jump is the COVID rebound: 2020-21 was a remote year
    # with near-zero removals district-wide (MPS total was 18), so almost any
    # school "increases" massively returning to normal. Flag those so a reporter
    # doesn't mistake the rebound for a real accountability trend. Non-2021-22
    # spikes are the genuine leads.
    df = df.copy()
    df["note"] = df["year"].apply(
        lambda y: "COVID rebound from remote 2020-21 — likely artifact, not a lead"
        if y == "2021-22" else ""
    )
    return df[[
        "district", "school", "year", "value", "yoy_change", "pct_change",
        "_count", "enrollment", "note",
    ]].rename(columns={
        "value": "rate_per_100",
        "yoy_change": "rate_increase",
        "_count": "removals",
    })


def run():
    mke = load_school_rows()
    trend_df, latest_year, closure_count = build_trend(mke)

    flags_df = build_flags(trend_df)

    # category + unit added; enrollment kept after the shared enrollment-file
    # schema so the rate's base size travels with the trend.
    schema_cols = ["metric", "category", "unit", "district", "school", "group",
                   "year", "value", "yoy_change", "pct_change", "status_flag",
                   "enrollment"]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    trend_df[schema_cols].to_csv(PROCESSED_DIR / "discipline_school_trend.csv", index=False)
    flags_df.to_csv(PROCESSED_DIR / "discipline_school_flags.csv", index=False)

    all_rows = trend_df[trend_df["category"] == "all"]
    n_schools = all_rows.drop_duplicates(["district", "school"]).shape[0]
    suppressed_years = int(all_rows["status_flag"].str.contains("Suppressed", na=False).sum())
    small_years = int(all_rows["status_flag"].str.contains("Small count", na=False).sum())
    zero_years = int(all_rows["status_flag"].str.contains("Zero suspensions", na=False).sum())

    print(f"[ok] discipline_school: {n_schools} district/school groups, "
          f"{len(trend_df)} trend rows (3 categories), latest year {latest_year}")
    print(f"     [category=all] suppression-flagged years: {suppressed_years}")
    print(f"     [category=all] small-count (<10) years:   {small_years}")
    print(f"     [category=all] genuine-zero years:        {zero_years}")
    print(f"     closure-flagged groups:    {closure_count}")
    print(f"     rate-increase tip sheet:   {len(flags_df)} rows "
          f"-> {PROCESSED_DIR / 'discipline_school_flags.csv'}")
    return trend_df


if __name__ == "__main__":
    run()
