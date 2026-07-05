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


def _movers_from_frame(sub, metric, category, cfg):
    """Build the increase/decrease lists from one already-filtered frame
    (a single metric, and for discipline a single category)."""
    years = sorted(sub["year"].dropna().unique())
    if len(years) < 2:
        return pd.DataFrame()
    latest_year = years[-1]
    has_enroll_col = "enrollment" in sub.columns

    # Unit drives reliability + ranking. A rate needs both endpoints clean and
    # excludes small-count years; a raw count tolerates small numbers (that's
    # the point of showing expulsions as a count).
    unit = sub["unit"].iloc[0] if "unit" in sub.columns else None
    if unit == "count":
        exclude_flags, rank_mode = ("Suppressed",), "delta"
    elif unit == "rate":
        exclude_flags, rank_mode = ("Small count", "Suppressed"), "delta"
    else:
        exclude_flags, rank_mode = cfg["exclude_flags"], cfg["rank"]

    rows = []
    for (district, school), g in sub.groupby(["district", "school"]):
        if len(g) < 2:
            continue
        g = g.sort_values("year")
        last, prev = g.iloc[-1], g.iloc[-2]
        if last["year"] != latest_year:
            continue  # stopped reporting before the latest year
        lv, pv = last["value"], prev["value"]
        if pd.isna(lv) or pd.isna(pv):
            continue  # a gap — no honest change to rank

        base = last["enrollment"] if has_enroll_col else lv
        if cfg["min_enroll"] is not None and (pd.isna(base) or base < cfg["min_enroll"]):
            continue  # too few students for a per-100 rate to be stable
        if cfg["min_latest"] is not None and lv < cfg["min_latest"]:
            continue

        flags = f"{last.get('status_flag', '')} {prev.get('status_flag', '')}"
        if any(f in str(flags) for f in exclude_flags):
            continue
        delta = lv - pv
        pct = (delta / pv * 100) if pv != 0 else None
        rank = pct if rank_mode == "pct" else delta
        if rank is None or pd.isna(rank):
            continue
        rows.append({
            "metric": metric,
            "category": category,
            "unit": unit or "",
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
    return pd.concat([ups, downs]).drop(columns=["_rank"])


def movers_for(metric, cfg):
    path = PROCESSED_DIR / f"{metric}_school_trend.csv"
    if not path.exists():
        return None

    df = pd.read_csv(path)
    # discipline is long-format with a category column (all/suspension/
    # expulsion); everything else is single-series.
    categories = sorted(df["category"].unique()) if "category" in df.columns else [None]

    frames = []
    for cat in categories:
        sub = df[df["category"] == cat] if cat is not None else df
        frame = _movers_from_frame(sub, metric, cat, cfg)
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    cols = ["metric", "category", "unit", "direction", "district", "school",
            "enrollment", "prior_year", "latest_year", "prior_value",
            "latest_value", "change", "pct_change"]
    return out[[c for c in cols if c in out.columns]]


def run():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    for metric, cfg in CONFIG.items():
        out = movers_for(metric, cfg)
        if out is None:
            print(f"[skip] {metric}_school_movers: no {metric}_school_trend.csv yet")
            continue
        path = PROCESSED_DIR / f"{metric}_school_movers.csv"
        out.to_csv(path, index=False)
        if "category" in out.columns and out["category"].notna().any():
            by_cat = out.groupby("category")["direction"].value_counts().unstack(fill_value=0)
            summary = "; ".join(f"{c}: +{r.get('increase', 0)}/-{r.get('decrease', 0)}"
                                for c, r in by_cat.iterrows())
            print(f"[ok] {metric}_school_movers ({summary}) -> {path}")
        else:
            n_up = int((out["direction"] == "increase").sum())
            n_down = int((out["direction"] == "decrease").sum())
            print(f"[ok] {metric}_school_movers: {n_up} increases, {n_down} decreases -> {path}")


if __name__ == "__main__":
    run()
