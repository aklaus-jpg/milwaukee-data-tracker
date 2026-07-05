"""
analyze_school_forward.py — Wisconsin Forward Exam proficiency (grades 3-8),
the achievement spine. Fetches DPI's forward_certified files, computes % at each
performance level per school x student-group x subject x year, and derives
proficiency + disparity outputs that feed the dashboard's trend / movers /
disparity / scatter machinery.

DATA SHAPE (verified against the live catalog):
  forward_certified_<year>.zip holds TWO csvs (ELA/Reading/Writing and
  Mathematics/Science/Social Studies), same columns:
    ... DISTRICT_CODE, SCHOOL_CODE, DISTRICT_NAME, SCHOOL_NAME, TEST_SUBJECT,
    GRADE_LEVEL, TEST_RESULT, TEST_GROUP, GROUP_BY, GROUP_BY_VALUE,
    STUDENT_COUNT, GROUP_COUNT ...
  TEST_GROUP: Forward | DLM  (we keep Forward — the standard assessment).
  TEST_RESULT levels: Advanced, Meeting, Approaching, Developing (+ No Test /
    No Score / *). Proficient-and-above = Meeting + Advanced; "below" =
    Developing (the lowest level).

SUBJECTS: all four are ingested (ELA, Mathematics, Science, Social Studies) so a
future science story needs no re-ingest; the dashboard exposes ELA + Math only
(UI_SUBJECTS) for now. Reading/Writing (ELA sub-scores) are dropped.

SUPPRESSION (the careful part): aggregating grades 3-8, a suppressed cell ("*")
is EXCLUDED from both numerator and denominator — never treated as zero. So
% proficient = visible proficient / visible tested. Three DISTINCT empty states,
never collapsed:
  - "no tested grades" : school has no Forward rows for this subject (e.g. a high
    school) — it simply doesn't appear in the file (dashboard shows this state).
  - "suppressed"       : rows exist but every cell is redacted -> value = gap.
  - real 0%            : a computed zero, shown as 0.

Writes:
  data/raw/forward_raw.csv                     (Milwaukee, 4 subjects, Forward)
  data/processed/forward_school_trend.csv      (All Students, per subject)
  data/processed/forward_school_groups.csv     (per student group x subject)
  data/processed/forward_disparity.csv         (latest-year gap per school x
                                                subject x dimension)

Run directly:  python analyze_school_forward.py
"""
import io
import pathlib
import zipfile

import pandas as pd
import requests

import archive_raw
from analyze_school_enrollment import (
    COUNTY_FILTER,
    EXCLUDED_SCHOOL_NAMES,
    PLACEHOLDER_SCHOOL_NAMES,
    SCHOOL_NAME_ALIASES,
    normalize_district,
)

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")
BASE_URL = "https://dpi.wi.gov/sites/default/files/wise/downloads"

YEARS = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]

# TEST_SUBJECT -> canonical code. Reading/Writing (ELA sub-scores) intentionally
# excluded. All four canonical subjects are ingested; UI exposes UI_SUBJECTS.
SUBJECT_MAP = {"ELA": "ela", "Mathematics": "math", "Science": "science", "Social Studies": "social"}

LEVELS = ["Advanced", "Meeting", "Approaching", "Developing"]
PROFICIENT = {"Meeting", "Advanced"}
BELOW_LEVEL = "Developing"
SUPPRESSION_MARKERS = {"*", "[Data Suppressed]", "[Suppressed]"}

# Per dimension: the canonical pair to headline. focus/reference are assigned per
# school by value (higher vs lower) so the ratio is always >= 1 and reads
# naturally for an achievement gap (the higher-scoring group vs the lower).
DIMENSIONS = {
    "Race/Ethnicity": ("Black", "White"),
    "Gender": ("Male", "Female"),
    "Disability Status": ("SwD", "SwoD"),
    "EL Status": ("EL", "Eng Prof"),
    "Economic Status": ("Econ Disadv", "Not Econ Disadv"),
}
DROP_GROUP_VALUES = {"Unknown", "[Data Suppressed]"}
MIN_GROUP_TESTED = 20  # a group's proficiency needs a real base


def fetch():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    keep = ["SCHOOL_YEAR", "COUNTY", "DISTRICT_CODE", "SCHOOL_CODE", "DISTRICT_NAME",
            "SCHOOL_NAME", "TEST_SUBJECT", "GRADE_LEVEL", "TEST_RESULT", "TEST_GROUP",
            "GROUP_BY", "GROUP_BY_VALUE", "STUDENT_COUNT", "GROUP_COUNT"]
    frames = []
    for year in YEARS:
        url = f"{BASE_URL}/forward_certified_{year}.zip"
        try:
            r = requests.get(url, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(r.content))
        except (requests.RequestException, zipfile.BadZipFile) as e:
            print(f"    [skip] {year}: {e}")
            continue
        for m in zf.infolist():
            n = m.filename.lower()
            if not n.endswith(".csv") or "layout" in n:
                continue
            df = pd.read_csv(zf.open(m.filename), low_memory=False, dtype=str)
            df.columns = [c.strip() for c in df.columns]
            df = df[df["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)]
            df = df[(df["TEST_GROUP"] == "Forward") & (df["TEST_SUBJECT"].isin(SUBJECT_MAP))]
            frames.append(df[keep])
        print(f"    [ok] {year}")
    if not frames:
        print("[error] no Forward years fetched")
        return None
    out = pd.concat(frames, ignore_index=True)
    raw_path = RAW_DIR / "forward_raw.csv"
    out.to_csv(raw_path, index=False)
    print(f"  [saved] forward_raw.csv — {len(out)} Milwaukee rows")
    archive_raw.archive_raw(raw_path)  # dated, checksummed provenance copy
    return out


def load_rows():
    df = pd.read_csv(RAW_DIR / "forward_raw.csv", low_memory=False, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df = df[~df["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    df = df[~df["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]
    df["subject"] = df["TEST_SUBJECT"].map(SUBJECT_MAP)
    df["district"] = df["DISTRICT_NAME"].apply(normalize_district)
    df["school"] = df["SCHOOL_NAME"].astype(str).str.strip().replace(SCHOOL_NAME_ALIASES)
    df["dimension"] = df["GROUP_BY"]
    df["group"] = df["GROUP_BY_VALUE"].astype(str).str.strip()
    df["cnt_raw"] = df["STUDENT_COUNT"].astype(str).str.strip()
    df["cnt"] = pd.to_numeric(df["STUDENT_COUNT"], errors="coerce")
    return df


def proficiency(g):
    """Aggregate one school x subject x group across grades.
    Returns dict: value (% proficient or None), below (% Developing or None),
    tested, suppressed (bool), any_rows (bool). Suppressed cells excluded from
    both numerator and denominator (never zeroed)."""
    lvl = g[g["TEST_RESULT"].isin(LEVELS)]
    if lvl.empty:
        return {"value": None, "below": None, "tested": 0, "suppressed": False, "any_rows": False}
    suppressed_any = lvl["cnt_raw"].isin(SUPPRESSION_MARKERS).any()
    by_level = lvl.groupby("TEST_RESULT")["cnt"].sum(min_count=1)
    tested = float(by_level.sum())  # sum of visible counts across the 4 levels
    if pd.isna(tested) or tested <= 0:
        # rows exist but nothing visible -> suppressed gap
        return {"value": None, "below": None, "tested": 0, "suppressed": True, "any_rows": True}
    prof = float(by_level.reindex(list(PROFICIENT)).sum())
    below = float(by_level.get(BELOW_LEVEL, 0.0) or 0.0)
    return {
        "value": round(100 * prof / tested, 1),
        "below": round(100 * below / tested, 1),
        "tested": tested,
        "suppressed": bool(suppressed_any),
        "any_rows": True,
    }


def build_trend(df):
    rows = []
    keys = ["dimension", "subject", "district", "school", "SCHOOL_YEAR", "group"]
    for (dimension, subject, district, school, year, group), g in df.groupby(keys):
        if dimension != "All Students" and group in DROP_GROUP_VALUES:
            continue
        p = proficiency(g)
        if not p["any_rows"]:
            continue
        notes = []
        if p["value"] is None:
            notes.append("Suppressed (DPI privacy) — not zero")
        elif p["suppressed"]:
            notes.append("Some cells suppressed — computed from visible students")
        rows.append({
            "metric": "forward", "dimension": dimension, "group": group,
            "subject": subject, "unit": "pct", "district": district, "school": school,
            "year": year, "value": p["value"], "pct_below": p["below"],
            "tested": None if not p["tested"] else int(p["tested"]),
            "status_flag": "; ".join(notes),
        })
    return pd.DataFrame(rows)


def with_yoy(trend):
    """Add yoy/pct on the % proficient series, per school x subject x group."""
    trend = trend.sort_values("year").copy()
    trend["yoy_change"] = None
    trend["pct_change"] = None
    for _, idx in trend.groupby(["district", "school", "subject", "group"]).groups.items():
        s = trend.loc[idx].sort_values("year")
        v = s["value"].astype("float64")
        yoy = v.diff()
        pct = (v.pct_change() * 100).replace([float("inf"), float("-inf")], pd.NA)
        trend.loc[s.index, "yoy_change"] = yoy.values
        trend.loc[s.index, "pct_change"] = pct.values
    return trend


def build_disparity(groups_df):
    """Latest-year proficiency gap per school x subject x dimension. focus = the
    higher-scoring group of the canonical pair, so the ratio is >= 1 and reads as
    'White proficient 3x Black'."""
    latest = groups_df["year"].max()
    cur = groups_df[(groups_df["year"] == latest) & (groups_df["value"].notna())
                    & (groups_df["tested"] >= MIN_GROUP_TESTED)]
    out = []
    for (subject, dimension, district, school), g in cur.groupby(
            ["subject", "dimension", "district", "school"]):
        if dimension not in DIMENSIONS:
            continue
        rates = dict(zip(g["group"], g["value"]))
        tested = dict(zip(g["group"], g["tested"]))
        rated = {k: v for k, v in rates.items() if v is not None}
        if len(rated) < 2:
            continue
        top = max(rated, key=rated.get)
        bot = min(rated, key=rated.get)
        high_low = round(rated[top] / rated[bot], 2) if rated[bot] > 0 else None
        # Headline ratio is strictly the canonical pair (e.g. White vs Black),
        # null unless BOTH are rateable — so the leaderboard is a clean, like-for-
        # like comparison, not a fallback to whatever two groups exist.
        a, b = DIMENSIONS[dimension]
        if a in rated and b in rated:
            focus, reference = (a, b) if rated[a] >= rated[b] else (b, a)
            fr = round(rated[focus] / rated[reference], 2) if rated[reference] > 0 else None
            f_rate, r_rate = rated[focus], rated[reference]
        else:
            focus, reference, fr, f_rate, r_rate = a, b, None, rated.get(a), rated.get(b)
        out.append({
            "metric": "forward", "subject": subject, "dimension": dimension,
            "district": district, "school": school, "year": latest,
            "focus_group": focus, "focus_rate": f_rate,
            "reference_group": reference, "reference_rate": r_rate,
            "focus_reference_ratio": fr,
            # tested N behind each side of the gap — so a "biggest gap" school
            # can't hide that its smaller group is a tiny sample.
            "focus_n": None if tested.get(focus) is None else int(tested.get(focus)),
            "reference_n": None if tested.get(reference) is None else int(tested.get(reference)),
            "highest_group": top, "highest_rate": rated[top],
            "lowest_group": bot, "lowest_rate": rated[bot],
            "high_low_ratio": high_low,
        })
    df = pd.DataFrame(out)
    if not df.empty:
        df = df.sort_values(["subject", "dimension", "focus_reference_ratio"],
                            ascending=[True, True, False], na_position="last")
    return df


def run(do_fetch=True):
    if do_fetch or not (RAW_DIR / "forward_raw.csv").exists():
        if fetch() is None and not (RAW_DIR / "forward_raw.csv").exists():
            print("[skip] forward: no raw data")
            return
    df = load_rows()
    trend = with_yoy(build_trend(df))

    all_students = trend[trend["dimension"] == "All Students"].copy()
    groups = trend.copy()
    disparity = build_disparity(groups)

    schema = ["metric", "subject", "unit", "district", "school", "group", "year",
              "value", "pct_below", "yoy_change", "pct_change", "status_flag", "tested"]
    grp_schema = ["metric", "dimension", "group", "subject", "unit", "district",
                  "school", "year", "value", "pct_below", "tested", "status_flag"]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    all_students[schema].to_csv(PROCESSED_DIR / "forward_school_trend.csv", index=False)
    groups[grp_schema].to_csv(PROCESSED_DIR / "forward_school_groups.csv", index=False)
    disparity.to_csv(PROCESSED_DIR / "forward_disparity.csv", index=False)

    n_schools = all_students.drop_duplicates(["district", "school"]).shape[0]
    latest = trend["year"].max()
    print(f"[ok] forward: {n_schools} schools with tested grades, {len(trend)} rows "
          f"(subject x group x year), latest {latest}")
    print(f"     all-students trend rows: {len(all_students)}, group rows: {len(groups)}")
    print(f"     disparity rows: {len(disparity)}")
    return trend


if __name__ == "__main__":
    run()
