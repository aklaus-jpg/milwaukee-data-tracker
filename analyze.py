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


def analyze_source(name, src, geo_filter, geo_col_key, threshold, min_group_size):
    raw_path = RAW_DIR / f"{name}_raw.csv"
    if not raw_path.exists():
        print(f"[skip] {name}: raw file not found — run fetch_dpi.py first")
        return None

    df = pd.read_csv(raw_path, low_memory=False)
    df.columns = [c.strip() for c in df.columns]

    geo_col = src.get(geo_col_key)
    year_col = src["year_col"]
    group_col = src.get("group_col")
    group_value_col = src.get("group_value_col")
    value_col = src["value_col"]
    school_col = src.get("school_col")
    size_col = src.get("size_col")  # optional: separate column to judge group size by, if value_col itself isn't a headcount

    required = [geo_col, year_col, value_col]
    if size_col:
        required.append(size_col)
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"[error] {name}: missing expected columns {missing}. "
              f"Run: python inspect_csv.py {raw_path}")
        return None

    mask = df[geo_col].astype(str).str.contains(geo_filter, case=False, na=False)
    mps = df[mask].copy()

    if mps.empty:
        print(f"[warn] {name}: no rows matched geographic filter '{geo_filter}' "
              f"in column '{geo_col}' — check inspect_csv.py output")
        return None

    # DPI redacts small counts for student privacy (shows "*" or similar instead
    # of a number when a group is small enough to be identifiable). Coerce to
    # numeric and track how many rows got redacted so it's visible, not silent.
    raw_values = mps[value_col]
    mps[value_col] = pd.to_numeric(raw_values, errors="coerce")
    redacted_count = mps[value_col].isna().sum() - raw_values.isna().sum()
    if redacted_count > 0:
        print(f"  [note] {name}: {redacted_count} row(s) had a redacted/non-numeric "
              f"value (e.g. '*') for student privacy — excluded from sums/rates")

    if size_col:
        mps[size_col] = pd.to_numeric(mps[size_col], errors="coerce")

    # Prefer district-level rows (avoid double-counting individual schools) if a
    # school column exists and has an identifiable "district total" style row.
    if school_col and school_col in mps.columns:
        # DPI's district roll-up row is SCHOOL_NAME "[Districtwide]" (older/other
        # files use a blank school or "District Total"). It already equals the sum
        # of the district's schools, so if we keep BOTH the roll-up and the school
        # rows every district total doubles (MPS read 131,198 instead of 65,599).
        # Prefer the authoritative roll-up row and drop the per-school rows.
        school_names = mps[school_col].astype(str).str.strip()
        district_level = mps[
            mps[school_col].isna()
            | school_names.str.contains("District Total", case=False, na=False)
            | school_names.isin(["[Districtwide]", "[Statewide]"])
        ]
        if not district_level.empty:
            mps = district_level

    # County filtering pulls in multiple districts (MPS, Wauwatosa, Shorewood,
    # charters, etc.). Track each district's overall trend separately — not
    # blended into one county-wide average, which would hide exactly the
    # district-to-district comparisons a reporter would want, and not crossed
    # with every demographic subgroup either, which would multiply into
    # hundreds of mostly-tiny combinations. Label is "District Name — All
    # Students" so MPS and, say, Wauwatosa both show up clearly and
    # comparably. For demographic breakdowns within a single district, narrow
    # county_filter down to that district's name instead.
    district_col = src.get("district_col")
    has_district_split = district_col and district_col in mps.columns

    group_frames = {}
    if has_district_split:
        for dist, d_df in mps.groupby(district_col):
            if group_col in d_df.columns and group_value_col in d_df.columns:
                overall = d_df[d_df[group_value_col].astype(str).str.contains("All Students", case=False, na=False)]
                d_df = overall if not overall.empty else d_df
            group_frames[f"{dist} — All Students"] = d_df
    elif group_col in mps.columns and group_value_col in mps.columns:
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

        # group size per year, for flag-gating: use the dedicated size_col if
        # configured, otherwise fall back to the value itself for count metrics
        # (e.g. enrollment, where the value IS the headcount).
        if size_col:
            yearly_size = g_df.groupby(year_col)[size_col].sum(min_count=1)
        elif src["metric_type"] == "count":
            yearly_size = yearly
        else:
            yearly_size = None

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
            if not (pd.notna(pct) and abs(pct) >= threshold):
                continue
            if yearly_size is not None:
                size_this_year = yearly_size.get(yr)
                if pd.isna(size_this_year) or size_this_year < min_group_size:
                    continue  # too small a group — skip flagging, still in trend CSV
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
    min_group_size = cfg.get("min_group_size_for_flag", 0)
    geo_filter = cfg.get("county_filter", "Milwaukee")
    geo_col_key = "county_col"

    all_flags = []
    for name, src in cfg["sources"].items():
        result = analyze_source(name, src, geo_filter, geo_col_key, threshold, min_group_size)
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
