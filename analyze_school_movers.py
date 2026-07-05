"""
analyze_school_movers.py — "biggest movers" reporting export for each
school-level metric: the schools with the largest latest-year vs prior-year
change, in both directions.

This is the downloadable companion to the dashboard's "Biggest movers" panel
(docs/index.html) and uses the SAME definition so the two never disagree:
  - latest-year value vs the immediately prior year (not across the full span,
    which for discipline would surface the 2020-21 COVID remote-year rebound),
  - discipline ranked by absolute rate-point change (it is already a rate),
    enrollment ranked by percent change (a headcount needs normalizing),
  - reliability gates: skip suppressed/small-count discipline years, drop
    enrollments under 50 whose percentages swing on a handful of students, and
    require a ~100-student base for discipline so a per-100 rate is stable.

A spike is a reporting LEAD, not a fact — verify directly before publishing.

Writes: data/processed/<metric>_school_movers.csv
  columns: metric, direction, district, school, enrollment, prior_year,
           latest_year, prior_value, latest_value, change, pct_change

Run directly:  python analyze_school_movers.py
"""
import pathlib

import pandas as pd

PROCESSED_DIR = pathlib.Path("data/processed")

TOP_N = 15  # per direction

# A per-100 rate needs enough students behind it to be stable — a big swing on
# ~40 students is noise, not a trend. Require a real base for discipline movers.
DISCIPLINE_ENROLL_FLOOR = 100

CONFIG = {
    "discipline": {"rank": "delta", "min_latest": None,
                   "min_enroll": DISCIPLINE_ENROLL_FLOOR,
                   "exclude_flags": ("Small count", "Suppressed")},
    "enrollment": {"rank": "pct", "min_latest": 50, "min_enroll": None,
                   "exclude_flags": ()},
}


def movers_for(metric, cfg):
    path = PROCESSED_DIR / f"{metric}_school_trend.csv"
    if not path.exists():
        return None

    df = pd.read_csv(path)
    years = sorted(df["year"].dropna().unique())
    if len(years) < 2:
        return pd.DataFrame()
    latest_year, prior_year = years[-1], years[-2]

    # discipline carries a real enrollment column; enrollment's own value IS the
    # headcount, so fall back to that as the base size.
    has_enroll_col = "enrollment" in df.columns

    rows = []
    for (district, school), g in df.groupby(["district", "school"]):
        if len(g) < 2:
            continue  # need a prior year to compute a change
        g = g.sort_values("year")
        last, prev = g.iloc[-1], g.iloc[-2]
        if last["year"] != latest_year:
            continue  # school stopped reporting before the latest year
        lv, pv = last["value"], prev["value"]
        if pd.isna(lv) or pd.isna(pv):
            continue  # a gap (suppressed / missing) — no honest change to rank
        if cfg["min_latest"] is not None and lv < cfg["min_latest"]:
            continue

        base = last["enrollment"] if has_enroll_col else lv
        if cfg["min_enroll"] is not None and (pd.isna(base) or base < cfg["min_enroll"]):
            continue  # too few students for a per-100 rate to be stable

        flags = f"{last.get('status_flag', '')} {prev.get('status_flag', '')}"
        if any(f in str(flags) for f in cfg["exclude_flags"]):
            continue
        delta = lv - pv
        pct = (delta / pv * 100) if pv != 0 else None
        rank = pct if cfg["rank"] == "pct" else delta
        if rank is None or pd.isna(rank):
            continue
        rows.append({
            "metric": metric,
            "district": district,
            "school": school,
            "enrollment": None if pd.isna(base) else int(round(base)),
            "prior_year": prev["year"],
            "latest_year": last["year"],
            "prior_value": round(pv, 2),
            "latest_value": round(lv, 2),
            "change": round(delta, 2),
            "pct_change": None if pct is None else round(pct, 1),
            "_rank": rank,
        })

    if not rows:
        return pd.DataFrame()

    mv = pd.DataFrame(rows).sort_values("_rank", ascending=False)
    ups = mv[mv["_rank"] > 0].head(TOP_N).assign(direction="increase")
    downs = mv[mv["_rank"] < 0].tail(TOP_N).sort_values("_rank").assign(direction="decrease")
    out = pd.concat([ups, downs]).drop(columns=["_rank"])
    cols = ["metric", "direction", "district", "school", "enrollment",
            "prior_year", "latest_year", "prior_value", "latest_value",
            "change", "pct_change"]
    return out[cols]


def run():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    for metric, cfg in CONFIG.items():
        out = movers_for(metric, cfg)
        if out is None:
            print(f"[skip] {metric}_school_movers: no {metric}_school_trend.csv yet")
            continue
        path = PROCESSED_DIR / f"{metric}_school_movers.csv"
        out.to_csv(path, index=False)
        n_up = int((out["direction"] == "increase").sum())
        n_down = int((out["direction"] == "decrease").sum())
        print(f"[ok] {metric}_school_movers: {n_up} increases, {n_down} decreases -> {path}")


if __name__ == "__main__":
    run()
