"""
find_rename_candidates.py — QA / lead tool (NOT part of the weekly pipeline).

Surfaces likely school relabelings that the name-keyed pipeline hasn't stitched:
a school whose data ENDS in year N sitting next to a different-named school in
the SAME district whose data STARTS in year N+1, with continuous enrollment
across the gap. That pattern is the signature of a rename / label change (like
Vincent High -> Harold S Vincent), which otherwise renders as a false closure
plus an apparently brand-new school.

Runs on the PROCESSED enrollment trend (post-alias, post-stitch), so schools
already fixed via SCHOOL_NAME_ALIASES or authorizer-stitching don't reappear —
you only see the candidates still worth confirming.

Every hit is a LEAD, not a fact: a closer/opener pair can be a genuine
closure + unrelated new school, a co-location, or a real rename. Confirm against
DPI / MPS before adding a SCHOOL_NAME_ALIASES entry.

Run directly:  python find_rename_candidates.py
"""
import pathlib
import re

import pandas as pd

PROCESSED_DIR = pathlib.Path("data/processed")

# Enrollment-continuity band: a relabeled school keeps most of its students, so
# the opener's first-year headcount should be close to the closer's last-year
# headcount. Loose enough to catch a grade reconfiguration, tight enough to cut
# noise.
RATIO_LO, RATIO_HI = 0.5, 2.0
MIN_ENROLL = 20  # ignore tiny programs whose counts are noise either way

# Words too generic to count as a meaningful shared-name signal.
STOPWORDS = {
    "school", "high", "elementary", "middle", "academy", "of", "the", "for",
    "and", "charter", "college", "prep", "preparatory", "public", "campus",
    "center", "community", "international", "arts", "science", "sciences",
    "technology", "learning", "education", "language", "grade", "k8", "k12",
}


def significant_words(name):
    words = re.findall(r"[a-z0-9]+", str(name).lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def endpoints(df):
    df = df.sort_values("year")
    return pd.Series({
        "first_yr": df["year"].iloc[0],
        "last_yr": df["year"].iloc[-1],
        "first_val": df["value"].iloc[0],
        "last_val": df["value"].iloc[-1],
    })


def run():
    path = PROCESSED_DIR / "enrollment_school_trend.csv"
    if not path.exists():
        print("[skip] no enrollment_school_trend.csv — run analyze_school_enrollment.py first")
        return

    e = pd.read_csv(path)
    years = sorted(e["year"].dropna().unique())
    next_year = {years[i]: years[i + 1] for i in range(len(years) - 1)}
    first_year, last_year = years[0], years[-1]

    info = (e.groupby(["district", "school"])[["year", "value"]]
            .apply(endpoints).reset_index())

    closers = info[info["last_yr"] < last_year]
    openers = info[info["first_yr"] > first_year]

    candidates = []
    for _, c in closers.iterrows():
        gap_open = next_year.get(c["last_yr"])
        if gap_open is None:
            continue
        pool = openers[(openers["district"] == c["district"])
                       & (openers["first_yr"] == gap_open)]
        for _, o in pool.iterrows():
            if o["school"] == c["school"]:
                continue
            cv, ov = c["last_val"], o["first_val"]
            if pd.isna(cv) or pd.isna(ov) or cv < MIN_ENROLL or ov < MIN_ENROLL:
                continue
            ratio = ov / cv
            if not (RATIO_LO <= ratio <= RATIO_HI):
                continue
            shared = significant_words(c["school"]) & significant_words(o["school"])
            candidates.append({
                "district": c["district"],
                "closed_school": c["school"],
                "last_yr": c["last_yr"],
                "last_enroll": int(cv),
                "opened_school": o["school"],
                "first_yr": o["first_yr"],
                "first_enroll": int(ov),
                "ratio": round(ratio, 2),
                "shared_words": ", ".join(sorted(shared)),
            })

    if not candidates:
        print("No rename candidates found.")
        return

    df = pd.DataFrame(candidates)
    # Rank: shared name words first (strong signal), then enrollment closeness.
    df["_namehit"] = (df["shared_words"] != "").astype(int)
    df["_closeness"] = (df["ratio"] - 1).abs()
    df = df.sort_values(["_namehit", "_closeness"], ascending=[False, True]).drop(
        columns=["_namehit", "_closeness"])

    out = PROCESSED_DIR / "rename_candidates.csv"
    df.to_csv(out, index=False)
    print(f"[ok] {len(df)} candidate pair(s) -> {out}\n")

    for _, r in df.iterrows():
        flag = "  << shared name" if r["shared_words"] else ""
        print(f"{r['district']}{flag}")
        print(f"    closed: {r['closed_school']} (ends {r['last_yr']}, {r['last_enroll']} students)")
        print(f"    opened: {r['opened_school']} (starts {r['first_yr']}, {r['first_enroll']} students)")
        print(f"    enroll ratio {r['ratio']}"
              + (f" | shared words: {r['shared_words']}" if r["shared_words"] else "")
              + "\n")


if __name__ == "__main__":
    run()
