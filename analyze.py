"""
analyze.py — filters raw DPI files down to Milwaukee (MPS), computes year-over-year
change per metric/group, flags changes above the configured threshold, and writes:
  - data/processed/<metric>_trend.csv   (full year-by-year series, all groups)
  - data/processed/<metric>_flags.csv   (just the years that crossed the threshold)
  - data/processed/all_flags.csv        (every flagged change, combined)
  - SUMMARY.md                          (human-readable digest of flags)

Run directly:  python analyze.py
"""
import pathlib
import yaml
import pandas as pd

RAW_DIR = pathlib.Path("data/raw")
PROCESSED_DIR = pathlib.Path("data/processed")


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def analyze_source(name, src, district_filter, threshold):
    raw_path = RAW_DIR / f"{name}_raw.csv"
    if not raw_path.exists():
        print(f"[skip] {name}: raw file not found — run fetch_dpi.py first")
        return None

    df = pd.read_csv(raw_path, low_memory=False)
    df.columns = [c.strip() for c in df.columns]

    district_col = src["district_col"]
    year_col = src["year_col"]
    group_col = src.get("group_col")
    group_value_col = src.get("group_value_col")
    value_col = src["value_col"]
    school_col = src.get("school_col")

    missing = [c for c in [district_col, year_col, value_col] if c not in df.columns]
    if missing:
        print(f"[error] {name}: missing expected columns {missing}. "
              f"Run: python inspect_csv.py {raw_path}")
        return None

    mask = df[district_col].astype(str).str.contains(district_filter, case=False, na=False)
    mps = df[mask].copy()

    if mps.empty:
        print(f"[warn] {name}: no rows matched district filter '{district_filter}' "
              f"in column '{district_col}' — check inspect_csv.py output")
        return None

    # Prefer district-level rows (avoid double-counting individual schools) if a
    # school column exists and has an identifiable "district total" style row.
    if school_col and school_col in mps.columns:
        district_level = mps[
            mps[school_col].isna()
            | mps[school_col].astype(str).str.contains("District Total", case=False, na=False)
        ]
        if not district_level.empty:
            mps = district_level

    group_frames = {}
    if group_col in mps.columns and group_value_col in mps.columns:
        overall = mps[mps[group_value_col].astype(str).str.contains("All Students", case=False, na=False)]
        group_frames["All Students"] = overall if not overall.empty else mps
        for g_val, g_df in mps.groupby(group_value_col):
            group_frames[str(g_val)] = g_df
    else:
        group_frames["All Students"] = mps

    trend_rows, flag_rows = [], []

    for label, g_df in group_frames.items():
        if src["metric_type"] == "count":
            yearly = g_df.groupby(year_col)[value_col].sum(min_count=1)
        else:
            yearly = g_df.groupby(year_col)[value_col].mean()

        yearly = yearly.sort_index()
        yoy = yearly.diff()
        pct_change = yearly.pct_change() * 100

        for yr, val in yearly.items():
            trend_rows.append({
                "metric": name,
                "group": label,
                "year": yr,
                "value": val,
                "yoy_change": yoy.get(yr),
                "pct_change": pct_change.get(yr),
            })

        for yr, pct in pct_change.items():
            if pd.notna(pct) and abs(pct) >= threshold:
                flag_rows.append({
                    "metric": name,
                    "group": label,
                    "year": yr,
                    "value": yearly.get(yr),
                    "pct_change": round(pct, 1),
                })

    trend_df = pd.DataFrame(trend_rows)
    flag_df = pd.DataFrame(flag_rows)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    trend_df.to_csv(PROCESSED_DIR / f"{name}_trend.csv", index=False)
    flag_df.to_csv(PROCESSED_DIR / f"{name}_flags.csv", index=False)

    print(f"[ok] {name}: {len(trend_df)} trend rows, {len(flag_df)} flagged changes")
    return trend_df, flag_df


def write_summary(flags_df):
    lines = ["# Weekly Data Flag Summary", ""]
    if flags_df.empty:
        lines.append("No year-over-year changes crossed the threshold this run.")
    else:
        sorted_flags = flags_df.reindex(
            flags_df["pct_change"].abs().sort_values(ascending=False).index
        )
        for _, row in sorted_flags.iterrows():
            direction = "up" if row["pct_change"] > 0 else "down"
            lines.append(
                f"- **{row['metric']}** ({row['group']}) {direction} "
                f"{abs(row['pct_change'])}% in {row['year']} (value: {row['value']})"
            )
    pathlib.Path("SUMMARY.md").write_text("\n".join(lines) + "\n")


def run_all():
    cfg = load_config()
    threshold = cfg.get("flag_threshold_pct", 5)
    district_filter = cfg.get("district_filter", "Milwaukee")

    all_flags = []
    for name, src in cfg["sources"].items():
        result = analyze_source(name, src, district_filter, threshold)
        if result:
            _, flags = result
            all_flags.append(flags)

    combined = pd.concat(all_flags, ignore_index=True) if all_flags else pd.DataFrame(
        columns=["metric", "group", "year", "value", "pct_change"]
    )
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_csv(PROCESSED_DIR / "all_flags.csv", index=False)
    write_summary(combined)


if __name__ == "__main__":
    run_all()
