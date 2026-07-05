"""
analyze_school_hs_completion.py — High school completion (grad rate).

Headline = 4-year REGULAR-diploma rate: Completed-Regular-High-School-Diploma
STUDENT_COUNT / COHORT_COUNT, at TIMEFRAME "4-Year rate". Verified against
WISEdash: Riverside class of 2024 = 185/227 = 81.5%; MPS [Districtwide] = 3521/
5182 = 67.9%. COHORT_COUNT is the denominator (all completion statuses sum to it).

Records are keyed by COHORT (graduating class), not school year: within any one
file each TIMEFRAME is a DIFFERENT cohort (4-Year = class of 2024, 5-Year = 2023,
…). So "year" here is the grad-class year. Across fetched files a cohort accrues
longer timeframes, which gives the SECONDARY field: the same cohort's regular-
diploma rate at its longest available timeframe (ext_rate/ext_timeframe). The
4-yr-vs-extended divergence is the high-mobility-completion story.

Conventions (confirmed against the file, not assumed):
  - Weighting: DPI's [Districtwide] row is student-weighted — use it for any
    district figure, never average school rates. (Tracker is school-level, so
    [Districtwide] is dropped from output as a placeholder.)
  - Suppression is a COMPLETION_STATUS="*" row (STUDENT_COUNT also "*"), and
    COHORT_COUNT itself can be "*". Gap ONLY when the NUMERATOR (regular diploma)
    or the cohort count is masked — a suppressed MINOR status (HSED, etc.) must
    NOT blank a computable diploma rate. So: if a real Regular-Diploma row is
    present, the rate is computable even with a "*" on some other status; if no
    Regular-Diploma row AND a "*" row exists, the diploma count may be the masked
    one -> gap; if no Regular-Diploma row and no "*" -> a genuine 0%.
  - Truncate to 1 decimal at DISPLAY only; store 4-dp precision (same as ACT).

Writes: hs_completion_raw.csv, hs_completion_school_trend.csv,
hs_completion_school_groups.csv, hs_completion_disparity.csv
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
YEARS = ["2017-18", "2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]

REGULAR = "Completed - Regular High School Diploma"
TIMEFRAMES = ["4-Year rate", "5-Year rate", "6-Year rate", "7-Year rate"]
HEADLINE_TF = "4-Year rate"
SUPPRESSION_MARKERS = {"*", "[Data Suppressed]", "[Suppressed]"}

DIMENSIONS = {
    "Race/Ethnicity": ("Black", "White"),
    "Gender": ("Male", "Female"),
    "Disability Status": ("SwD", "SwoD"),
    "EL Status": ("EL", "Eng Prof"),
    "Economic Status": ("Econ Disadv", "Not Econ Disadv"),
}
DROP_GROUP_VALUES = {"Unknown", "[Data Suppressed]"}
MIN_GROUP_COHORT = 20  # visibility floor: cohort size in the smaller group


def fetch():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    keep = ["SCHOOL_YEAR", "COUNTY", "DISTRICT_CODE", "SCHOOL_CODE", "DISTRICT_NAME",
            "SCHOOL_NAME", "COHORT", "COMPLETION_STATUS", "GROUP_BY", "GROUP_BY_VALUE",
            "TIMEFRAME", "COHORT_COUNT", "STUDENT_COUNT"]
    frames = []
    for year in YEARS:
        url = f"{BASE_URL}/hs_completion_certified_{year}.zip"
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
        frames.append(df[keep])
        print(f"    [ok] {year}")
    if not frames:
        print("[error] no HS completion years fetched")
        return None
    out = pd.concat(frames, ignore_index=True)
    raw_path = RAW_DIR / "hs_completion_raw.csv"
    out.to_csv(raw_path, index=False)
    print(f"  [saved] hs_completion_raw.csv — {len(out)} Milwaukee rows")
    archive_raw.archive_raw(raw_path)
    return out


def load_rows():
    df = pd.read_csv(RAW_DIR / "hs_completion_raw.csv", low_memory=False, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df = df[~df["SCHOOL_NAME"].isin(PLACEHOLDER_SCHOOL_NAMES)]
    df = df[~df["SCHOOL_NAME"].isin(EXCLUDED_SCHOOL_NAMES)]
    df["district"] = df["DISTRICT_NAME"].apply(normalize_district)
    df["school"] = df["SCHOOL_NAME"].astype(str).str.strip().replace(SCHOOL_NAME_ALIASES)
    df["dimension"] = df["GROUP_BY"]
    df["group"] = df["GROUP_BY_VALUE"].astype(str).str.strip()
    df["cnt_raw"] = df["STUDENT_COUNT"].astype(str).str.strip()
    df["cnt"] = pd.to_numeric(df["STUDENT_COUNT"], errors="coerce")
    df["cc"] = pd.to_numeric(df["COHORT_COUNT"], errors="coerce")
    return df


def diploma_rate(g):
    """One school x group x cohort x timeframe block. Regular-diploma rate over
    COHORT_COUNT, with the numerator-scoped gap rule."""
    cc = g["cc"].max()
    cc_masked = bool(g["COHORT_COUNT"].astype(str).str.strip().isin(SUPPRESSION_MARKERS).any())
    if cc_masked or pd.isna(cc) or cc <= 0:
        return {"value": None, "diploma": None, "cohort": None,
                "gap_reason": "denominator_suppressed" if cc_masked else "no_cohort"}

    reg = g[g["COMPLETION_STATUS"] == REGULAR]
    reg_cnt = reg["cnt"].dropna()
    if len(reg_cnt):                       # numerator known -> computable
        num = float(reg_cnt.sum())
        return {"value": round(100 * num / cc, 4), "diploma": num, "cohort": float(cc),
                "gap_reason": None}
    # no regular-diploma row: is it suppressed, or a genuine zero?
    has_star = bool((g["COMPLETION_STATUS"] == "*").any()
                    or g["cnt_raw"].isin(SUPPRESSION_MARKERS).any())
    if has_star:
        return {"value": None, "diploma": None, "cohort": float(cc),
                "gap_reason": "numerator_suppressed"}
    return {"value": 0.0, "diploma": 0.0, "cohort": float(cc), "gap_reason": None}  # genuine 0%


def build_trend(df):
    """One row per school x group x cohort: 4-year headline + longest-timeframe
    (same-cohort) extended rate."""
    rows = []
    keys = ["dimension", "district", "school", "COHORT", "group"]
    for (dimension, district, school, cohort, group), g in df.groupby(keys):
        if dimension != "All Students" and group in DROP_GROUP_VALUES:
            continue
        blocks = {tf: gg for tf, gg in g.groupby("TIMEFRAME")}
        if HEADLINE_TF not in blocks:
            continue
        head = diploma_rate(blocks[HEADLINE_TF])
        # Label by DPI's expected-graduation-year (the 4-Year block's SCHOOL_YEAR),
        # not the cohort integer, so our year labels match DPI's files exactly.
        head_year = str(blocks[HEADLINE_TF]["SCHOOL_YEAR"].iloc[0])

        # extended: same cohort, longest timeframe that is computable
        ext = {"value": None, "gap_reason": None, "tf": None}
        for tf in reversed(TIMEFRAMES):
            if tf == HEADLINE_TF or tf not in blocks:
                continue
            e = diploma_rate(blocks[tf])
            if e["value"] is not None:
                ext = {"value": e["value"], "gap_reason": e["gap_reason"], "tf": tf}
                break

        reason = head["gap_reason"]
        notes = []
        if reason == "numerator_suppressed":
            notes.append("Gapped: regular-diploma count suppressed by DPI — not zero")
        elif reason == "denominator_suppressed":
            notes.append("Gapped: cohort count suppressed by DPI")
        elif reason == "no_cohort":
            notes.append("No cohort count — not computable")
        rows.append({
            "metric": "hs_completion", "dimension": dimension, "group": group,
            "unit": "pct", "district": district, "school": school, "year": head_year,
            "cohort_class": str(cohort),
            "value": head["value"],
            "ext_rate": ext["value"], "ext_timeframe": ext["tf"],
            "diploma": None if head["diploma"] is None else int(head["diploma"]),
            "cohort_count": None if head["cohort"] is None else int(head["cohort"]),
            "gap_reason": reason or "", "status_flag": "; ".join(notes),
        })
    return pd.DataFrame(rows)


def with_yoy(trend):
    trend = trend.sort_values("year").copy()
    trend["yoy_change"] = None
    for _, idx in trend.groupby(["district", "school", "group"]).groups.items():
        s = trend.loc[idx].sort_values("year")
        trend.loc[s.index, "yoy_change"] = s["value"].astype("float64").diff().values
    return trend


def build_disparity(trend):
    latest = trend["year"].max()
    cur = trend[(trend["year"] == latest) & (trend["value"].notna())
                & (trend["cohort_count"] >= MIN_GROUP_COHORT)]
    out = []
    for (dimension, district, school), g in cur.groupby(["dimension", "district", "school"]):
        if dimension not in DIMENSIONS:
            continue
        rates = dict(zip(g["group"], g["value"]))
        cohort = dict(zip(g["group"], g["cohort_count"]))
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
            "metric": "hs_completion", "dimension": dimension,
            "district": district, "school": school, "year": latest,
            "focus_group": focus, "focus_rate": f_rate,
            "reference_group": reference, "reference_rate": r_rate,
            "focus_reference_ratio": fr,
            "focus_n": None if cohort.get(focus) is None else int(cohort.get(focus)),
            "reference_n": None if cohort.get(reference) is None else int(cohort.get(reference)),
            "highest_group": top, "highest_rate": rated[top],
            "lowest_group": bot, "lowest_rate": rated[bot],
            "high_low_ratio": high_low,
        })
    df = pd.DataFrame(out)
    if not df.empty:
        df = df.sort_values(["dimension", "focus_reference_ratio"],
                            ascending=[True, False], na_position="last")
    return df


def run(do_fetch=True):
    if do_fetch or not (RAW_DIR / "hs_completion_raw.csv").exists():
        if fetch() is None and not (RAW_DIR / "hs_completion_raw.csv").exists():
            print("[skip] hs_completion: no raw data")
            return
    df = load_rows()
    trend = with_yoy(build_trend(df))
    all_students = trend[trend["dimension"] == "All Students"].copy()
    disparity = build_disparity(trend)

    schema = ["metric", "unit", "district", "school", "group", "year", "cohort_class",
              "value", "ext_rate", "ext_timeframe", "diploma", "cohort_count", "yoy_change",
              "status_flag", "gap_reason"]
    grp_schema = ["metric", "dimension", "group", "unit", "district", "school", "year",
                  "cohort_class", "value", "ext_rate", "ext_timeframe", "diploma",
                  "cohort_count", "gap_reason", "status_flag"]

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    all_students[schema].to_csv(PROCESSED_DIR / "hs_completion_school_trend.csv", index=False)
    trend[grp_schema].to_csv(PROCESSED_DIR / "hs_completion_school_groups.csv", index=False)
    disparity.to_csv(PROCESSED_DIR / "hs_completion_disparity.csv", index=False)

    n = all_students.drop_duplicates(["district", "school"]).shape[0]
    print(f"[ok] hs_completion: {n} schools, {len(trend)} rows, latest cohort {trend['year'].max()}")
    print(f"     disparity rows: {len(disparity)}")
    return trend


if __name__ == "__main__":
    run()
