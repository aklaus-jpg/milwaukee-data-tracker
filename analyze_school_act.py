"""
analyze_school_act.py — ACT Statewide (all 11th graders) proficiency.

Same shape as Forward (verified against WISEdash): four performance levels
(Advanced/Meeting/Approaching/Developing) + No Test, per SUBJECT. The metric is
% proficient (Meeting+Advanced) over the FULL population (GROUP_COUNT, which
includes No Test), NOT over test-takers. Gate reproduced: MPS All Students ELA
2023-24 = (585+150)/4027 = 18.2%.

Differences from Forward, verified in the file:
  - TEST_GROUP: keep "ACT", drop "DLM" (the alternate assessment — a separate
    small population with its own GROUP_COUNT).
  - Within TEST_GROUP=ACT a level can be split across COLLEGE_READINESS rows
    (Meeting/Below + Meeting/CollegeReady) — summing STUDENT_COUNT by TEST_RESULT
    recombines them.
  - NO grade split (11th grade only), so pop is a single GROUP_COUNT and there is
    no partial-grade suppression: gap_reason is fully_suppressed or partial (some
    levels masked), never a grade artifact.
  - STANDARDS CHANGE at 2023-24 for ELA/Math/Science: no comparable results
    before 2023-24, so those subjects' series START at 2023-24 (a mover can't
    cross the break). Reading is treated as continuous (not flagged) — revisit if
    DPI says otherwise.

Suppression is the SAME "*"-row encoding as Forward (TEST_RESULT="*") -> gap the
group, with gap_reason (fully_suppressed / partial_suppression). Each subject's
AVERAGE_SCORE is stored as a secondary field (not headlined).

Writes: forward-style files, act_* prefix:
  data/raw/act_raw.csv, data/processed/act_school_trend.csv,
  act_school_groups.csv, act_disparity.csv

Run directly:  python analyze_school_act.py
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
YEARS = ["2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]

SUBJECT_MAP = {"ELA": "ela", "Mathematics": "math", "Reading": "reading", "Science": "science"}
LEVELS = ["Advanced", "Meeting", "Approaching", "Developing"]
PROFICIENT = {"Meeting", "Advanced"}
BELOW_LEVEL = "Developing"
SUPPRESSION_MARKERS = {"*", "[Data Suppressed]", "[Suppressed]"}

# ELA/Math/Science had a standards change at 2023-24 — earlier years aren't
# comparable, so their series start here. Reading treated as continuous.
STANDARDS_BREAK = {"ela", "math", "science"}
FIRST_COMPARABLE = "2023-24"

DIMENSIONS = {
    "Race/Ethnicity": ("Black", "White"),
    "Gender": ("Male", "Female"),
    "Disability Status": ("SwD", "SwoD"),
    "EL Status": ("EL", "Eng Prof"),
    "Economic Status": ("Econ Disadv", "Not Econ Disadv"),
}
DROP_GROUP_VALUES = {"Unknown", "[Data Suppressed]"}
MIN_GROUP_TESTED = 20


def fetch():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    keep = ["SCHOOL_YEAR", "COUNTY", "DISTRICT_CODE", "SCHOOL_CODE", "DISTRICT_NAME",
            "SCHOOL_NAME", "TEST_SUBJECT", "TEST_RESULT", "TEST_GROUP", "COLLEGE_READINESS",
            "GROUP_BY", "GROUP_BY_VALUE", "STUDENT_COUNT", "AVERAGE_SCORE", "GROUP_COUNT"]
    frames = []
    for year in YEARS:
        url = f"{BASE_URL}/act_statewide_certified_{year}.zip"
        try:
            r = requests.get(url, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            zf = zipfile.ZipFile(io.BytesIO(r.content))
        except (requests.RequestException, zipfile.BadZipFile) as e:
            print(f"    [skip] {year}: {e}")
            continue
        member = max((i for i in zf.infolist()
                      if i.filename.lower().endswith(".csv") and "layout" not in i.filename.lower()),
                     key=lambda i: i.file_size)
        df = pd.read_csv(zf.open(member.filename), low_memory=False, dtype=str)
        df.columns = [c.strip() for c in df.columns]
        df = df[df["COUNTY"].astype(str).str.contains(COUNTY_FILTER, case=False, na=False)]
        df = df[(df["TEST_GROUP"] == "ACT") & (df["TEST_SUBJECT"].isin(SUBJECT_MAP))]
        frames.append(df[keep])
        print(f"    [ok] {year}")
    if not frames:
        print("[error] no ACT years fetched")
        return None
    out = pd.concat(frames, ignore_index=True)
    raw_path = RAW_DIR / "act_raw.csv"
    out.to_csv(raw_path, index=False)
    print(f"  [saved] act_raw.csv — {len(out)} Milwaukee rows")
    archive_raw.archive_raw(raw_path)
    return out


def load_rows():
    df = pd.read_csv(RAW_DIR / "act_raw.csv", low_memory=False, dtype=str)
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
    df["gc"] = pd.to_numeric(df["GROUP_COUNT"], errors="coerce")
    df["avg"] = pd.to_numeric(df["AVERAGE_SCORE"], errors="coerce")
    return df


def proficiency(g):
    """One school x subject x group (no grades). % proficient over the full
    population (GROUP_COUNT). Any TEST_RESULT="*" row -> gap (never infer 0)."""
    has_star = bool((g["TEST_RESULT"] == "*").any() or g["cnt_raw"].isin(SUPPRESSION_MARKERS).any())
    if has_star:
        has_real = bool(g["TEST_RESULT"].isin(LEVELS).any())
        return {"value": None, "below": None, "notest": None, "avg": None, "tested": None,
                "pop": None, "gap_reason": "partial_suppression" if has_real else "fully_suppressed"}

    pop = g["gc"].max()  # single per-group total in the ACT test group
    if pd.isna(pop) or pop <= 0:
        return {"value": None, "below": None, "notest": None, "avg": None, "tested": None,
                "pop": None, "gap_reason": "no_population"}

    def count(levels):
        return float(g[g["TEST_RESULT"].isin(levels)]["cnt"].sum())

    prof = count(PROFICIENT)
    notest = count(["No Test"])
    avg = g["avg"].dropna()
    # 4-decimal precision; dashboard truncates to 1 dp for display (DPI convention).
    return {
        "value": round(100 * prof / pop, 4),
        "below": round(100 * count([BELOW_LEVEL]) / pop, 4),
        "notest": round(100 * notest / pop, 4),
        "avg": round(float(avg.iloc[0]), 1) if len(avg) else None,
        "tested": max(0.0, float(pop) - notest),
        "pop": float(pop),
        "gap_reason": None,
    }


def build_trend(df):
    rows = []
    keys = ["dimension", "subject", "district", "school", "SCHOOL_YEAR", "group"]
    for (dimension, subject, district, school, year, group), g in df.groupby(keys):
        if dimension != "All Students" and group in DROP_GROUP_VALUES:
            continue
        if subject in STANDARDS_BREAK and year < FIRST_COMPARABLE:
            continue  # pre-break years aren't comparable for these subjects
        p = proficiency(g)
        reason = p["gap_reason"]
        notes = []
        if reason == "fully_suppressed":
            notes.append("Suppressed by DPI (whole group redacted) — not zero")
        elif reason == "partial_suppression":
            notes.append("Gapped: a level was suppressed, can't compute the group")
        elif reason == "no_population":
            notes.append("No population count — not computable")
        rows.append({
            "metric": "act", "dimension": dimension, "group": group, "subject": subject,
            "unit": "pct", "district": district, "school": school, "year": year,
            "value": p["value"], "pct_below": p["below"], "pct_notest": p["notest"],
            "avg_score": p["avg"], "gap_reason": reason or "",
            "tested": None if not p["tested"] else int(p["tested"]),
            "population": None if p["pop"] is None else int(p["pop"]),
            "status_flag": "; ".join(notes),
        })
    return pd.DataFrame(rows)


def with_yoy(trend):
    trend = trend.sort_values("year").copy()
    trend["yoy_change"] = None
    trend["pct_change"] = None
    for _, idx in trend.groupby(["district", "school", "subject", "group"]).groups.items():
        s = trend.loc[idx].sort_values("year")
        v = s["value"].astype("float64")
        trend.loc[s.index, "yoy_change"] = v.diff().values
        trend.loc[s.index, "pct_change"] = (v.pct_change() * 100).replace(
            [float("inf"), float("-inf")], pd.NA).values
    return trend


def build_disparity(groups_df):
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
        a, b = DIMENSIONS[dimension]
        if a in rated and b in rated:
            focus, reference = (a, b) if rated[a] >= rated[b] else (b, a)
            fr = round(rated[focus] / rated[reference], 2) if rated[reference] > 0 else None
            f_rate, r_rate = rated[focus], rated[reference]
        else:
            focus, reference, fr, f_rate, r_rate = a, b, None, rated.get(a), rated.get(b)
        out.append({
            "metric": "act", "subject": subject, "dimension": dimension,
            "district": district, "school": school, "year": latest,
            "focus_group": focus, "focus_rate": f_rate,
            "reference_group": reference, "reference_rate": r_rate,
            "focus_reference_ratio": fr,
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
    if do_fetch or not (RAW_DIR / "act_raw.csv").exists():
        if fetch() is None and not (RAW_DIR / "act_raw.csv").exists():
            print("[skip] act: no raw data")
            return
    df = load_rows()
    trend = with_yoy(build_trend(df))
    all_students = trend[trend["dimension"] == "All Students"].copy()
    disparity = build_disparity(trend)

    schema = ["metric", "subject", "unit", "district", "school", "group", "year",
              "value", "pct_below", "pct_notest", "avg_score", "yoy_change", "pct_change",
              "status_flag", "gap_reason", "tested", "population"]
    grp_schema = ["metric", "dimension", "group", "subject", "unit", "district",
                  "school", "year", "value", "pct_below", "pct_notest", "avg_score",
                  "gap_reason", "tested", "population", "status_flag"]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    all_students[schema].to_csv(PROCESSED_DIR / "act_school_trend.csv", index=False)
    trend[grp_schema].to_csv(PROCESSED_DIR / "act_school_groups.csv", index=False)
    disparity.to_csv(PROCESSED_DIR / "act_disparity.csv", index=False)

    n = all_students.drop_duplicates(["district", "school"]).shape[0]
    print(f"[ok] act: {n} schools, {len(trend)} rows, latest {trend['year'].max()}")
    print(f"     disparity rows: {len(disparity)}")
    return trend


if __name__ == "__main__":
    run()
