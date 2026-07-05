"""
analyze_school_enrollment.py — builds school-level enrollment trends for Milwaukee
County schools, using the same yoy/pct-change method as analyze.py.

Writes: data/processed/enrollment_school_trend.csv
  columns: metric, district, school, group, year, value, yoy_change, pct_change, status_flag

Run directly:  python analyze_school_enrollment.py
"""
import pathlib
import re

import pandas as pd

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")

COUNTY_FILTER = "Milwaukee"
PLACEHOLDER_SCHOOL_NAMES = {"[Districtwide]", "[Statewide]", "[All]"}
SMALL_COUNT_THRESHOLD = 10  # DPI small-count privacy convention

# Multi-district virtual-school consortiums: several districts each buy seats
# in the same shared virtual school and report their own resident students
# against it in the same year (e.g. Brown Deer/Shorewood/Whitefish Bay all
# reporting a handful of students at "Between the Lakes Virtual Academy").
# These aren't Milwaukee County schools in the normal sense — exclude them
# entirely rather than showing near-zero counts scattered across districts.
EXCLUDED_SCHOOL_NAMES = {"Between the Lakes Virtual Academy", "Kiel eSchool"}

# One-off school-name change (not just a district/authorizer change) that the
# generic same-name stitching logic can't catch on its own: this MPS-chartered
# school was renamed when it became independently authorized in 2021-22,
# before being absorbed into the Carmen network in 2022-23 — confirmed by
# reviewing its continuous enrollment numbers across the rename.
SCHOOL_NAME_ALIASES = {
    "Carmen Middle School of Science and Technology South Campus": "Carmen Middle School South",
    # DPI labeled North Division with a trailing school-code ("0419") only in
    # 2018-19, then reverted to the plain name from 2019-20 on. Same MPS school —
    # without this alias the code suffix fragments the trend into a phantom
    # closure (0419) plus a school that looks like it opened in 2019-20.
    "North Division High 0419": "North Division High",
    # Same MPS school recorded under two labels: DPI used the short "Vincent
    # High" through 2022-23 and the full official "Harold S Vincent School of
    # Agricultural Science" from 2023-24 on (not a real rename — just incomplete
    # labeling in the source). Enrollment is continuous across the switch (685 in
    # 2022-23 -> 708 in 2023-24) and the two labels never co-occur in a year.
    # Without this alias "Vincent High" shows a false closure after 2022-23 and
    # the full name looks like a brand-new school. (Vincent Accelerated Academy
    # is a separate co-located alt program — not this.)
    "Vincent High": "Harold S Vincent School of Agricultural Science",
    # More DPI label expansions surfaced by find_rename_candidates.py — each is a
    # single school recorded under a short name in earlier years and its fuller
    # name later, with continuous enrollment across the switch and no year in
    # which both labels co-occur. Confirmed one per district (no cross-district
    # name collisions). Canonicalized to the current/fuller name.
    # (Grant Elementary -> U S Grant School deliberately NOT stitched pending
    #  direct confirmation.)
    "Meir School": "Golda Meir School",
    "James E Dottke Alternative School": "James E Dottke Project-Based Learning High School",
    "Luther Elementary": "E W Luther Elementary",
    "Maple Dale Elementary": "Maple Dale School",
    "Indian Hill Elementary": "Indian Hill School",
}

# Exact-match overrides applied after generic ", Inc."/"Inc."/"Inc" stripping.
# These are name/punctuation variants DPI has used across years for the same
# entity that the generic Inc-suffix strip doesn't catch on its own.
DISTRICT_NAME_OVERRIDES = {
    "Nicolet UHS": "Nicolet Union High School",
    "Central City Cyberschool": "Central City Cyberschool of Milwaukee",
    "Darrell Lynn Hines Academy": "Darrell L. Hines Academy",
    "Darrell L Hines Academy": "Darrell L. Hines Academy",
    "Dr Howard Fuller Collegiate Academy": "Dr. Howard Fuller Collegiate Academy",
    "Downtown Montessori": "Downtown Montessori Academy",
}


def normalize_district(name):
    s = str(name).strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r",?\s*Inc\.?\s*$", "", s).strip()
    s = re.sub(r"\s+", " ", s).rstrip(",").strip()
    if s.startswith("School for Early Development and"):
        return "School for Early Development and Achievement (SEDA)"
    return DISTRICT_NAME_OVERRIDES.get(s, s)


def load_school_rows():
    raw_path = RAW_DIR / "enrollment_raw.csv"
    df = pd.read_csv(raw_path, low_memory=False)
    df.columns = [c.strip() for c in df.columns]

    mke = df[df["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)].copy()

    # "All Students" total rows only — checked on both columns to exclude
    # demographic subgroups (Race/Ethnicity, Disability, Gender, etc.), which
    # also carry "All Students"-shaped values in GROUP_BY_VALUE in some rows.
    mke = mke[(mke["GROUP_BY"] == "All Students") & (mke["GROUP_BY_VALUE"] == "All Students")]

    mke = mke[~mke["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    mke = mke[~mke["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]

    raw_values = mke["STUDENT_COUNT"]
    mke["STUDENT_COUNT"] = pd.to_numeric(raw_values, errors="coerce")
    redacted_count = mke["STUDENT_COUNT"].isna().sum() - raw_values.isna().sum()
    if redacted_count > 0:
        print(f"  [note] enrollment_school: {redacted_count} row(s) had a redacted/non-numeric "
              f"value for student privacy — excluded from sums")

    mke["district"] = mke["DISTRICT_NAME"].apply(normalize_district)
    school_names = mke["SCHOOL_NAME"].astype(str).str.strip()
    mke["school"] = school_names.replace(SCHOOL_NAME_ALIASES)
    return mke


def find_stitchable_schools(mke):
    """A school name is "single-authorizer-per-year" if at most one district
    reports it in any given year. If that's true AND its authorizing district
    changes at some point (charter absorbed into a new authorizer, e.g.
    Bruce Guadalupe -> United Community Center in 2022-23), treat the whole
    history as one continuous school rather than fragmenting it at the
    authorizer boundary. Names that are reported by multiple districts in the
    SAME year (e.g. "Lincoln Elementary" in both Cudahy and Wauwatosa, every
    year) are coincidentally-named but genuinely different schools — never
    stitch those.
    """
    per_year_district_counts = mke.groupby(["school", "SCHOOL_YEAR"])["district"].nunique()
    max_districts_in_a_year = per_year_district_counts.groupby("school").max()
    single_authorizer = set(max_districts_in_a_year[max_districts_in_a_year <= 1].index)

    district_counts_overall = mke.groupby("school")["district"].nunique()
    changed_authorizer = set(district_counts_overall[district_counts_overall > 1].index)

    return single_authorizer & changed_authorizer


def build_school_trend_rows(school, district_by_year, yearly, latest_year, stitched):
    trend_rows = []
    yoy = yearly.diff()
    pct_change = yearly.pct_change() * 100

    last_year = yearly.index.max()
    is_closed_out = last_year < latest_year
    current_district = district_by_year.loc[last_year]

    prev_district = None
    for yr, val in yearly.items():
        this_district = district_by_year.loc[yr]
        notes = []
        if stitched and prev_district is not None and this_district != prev_district:
            notes.append(f"Authorizer/reporting changed to {this_district}, {yr}")
        prev_district = this_district

        if is_closed_out:
            notes.append(f"No data after {last_year}")
        if pd.notna(val) and val < SMALL_COUNT_THRESHOLD:
            notes.append("Small count (<10) — DPI privacy threshold")

        trend_rows.append({
            "metric": "enrollment",
            "district": current_district if stitched else this_district,
            "school": school,
            "group": f"{school} — All Students",
            "year": yr,
            "value": val,
            "yoy_change": yoy.get(yr),
            "pct_change": pct_change.get(yr),
            "status_flag": "; ".join(notes),
        })
    return trend_rows, is_closed_out


def build_trend(mke):
    latest_year = mke["SCHOOL_YEAR"].max()
    stitchable = find_stitchable_schools(mke)

    trend_rows = []
    closure_count = 0
    still_closed = []

    stitched_mask = mke["school"].isin(stitchable)

    # Stitched schools: group by school name only, spanning authorizer changes.
    for school, g_df in mke[stitched_mask].groupby("school"):
        yearly = g_df.groupby("SCHOOL_YEAR")["STUDENT_COUNT"].sum(min_count=1).sort_index()
        district_by_year = g_df.groupby("SCHOOL_YEAR")["district"].first()
        rows, is_closed_out = build_school_trend_rows(
            school, district_by_year, yearly, latest_year, stitched=True
        )
        trend_rows.extend(rows)
        if is_closed_out:
            closure_count += 1
            still_closed.append((district_by_year.loc[yearly.index.max()], school, yearly.index.max()))

    # Everything else: group by (district, school) as before.
    for (district, school), g_df in mke[~stitched_mask].groupby(["district", "school"]):
        yearly = g_df.groupby("SCHOOL_YEAR")["STUDENT_COUNT"].sum(min_count=1).sort_index()
        district_by_year = g_df.groupby("SCHOOL_YEAR")["district"].first()
        rows, is_closed_out = build_school_trend_rows(
            school, district_by_year, yearly, latest_year, stitched=False
        )
        trend_rows.extend(rows)
        if is_closed_out:
            closure_count += 1
            still_closed.append((district, school, yearly.index.max()))

    trend_df = pd.DataFrame(trend_rows)
    return trend_df, latest_year, closure_count, sorted(still_closed)


def run():
    mke = load_school_rows()
    trend_df, latest_year, closure_count, still_closed = build_trend(mke)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / "enrollment_school_trend.csv"
    trend_df.to_csv(out_path, index=False)

    closures_path = PROCESSED_DIR / "enrollment_school_closures.csv"
    pd.DataFrame(still_closed, columns=["district", "school", "last_year"]).to_csv(
        closures_path, index=False
    )

    n_schools = trend_df.drop_duplicates(["district", "school"]).shape[0]
    print(f"[ok] enrollment_school: {n_schools} district/school groups, {len(trend_df)} trend rows, "
          f"latest year {latest_year}, {closure_count} groups still flagged as closed after stitching "
          f"(full list: {closures_path})")
    return trend_df


if __name__ == "__main__":
    run()
