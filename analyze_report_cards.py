"""
analyze_report_cards.py — computes year-over-year change in each Milwaukee
County district's Overall Accountability Score, in the same trend/flag CSV
format as analyze.py, so it plugs into the same dashboard and SUMMARY.md
digest as enrollment/absenteeism/discipline.

Run directly:  python analyze_report_cards.py
"""
import pathlib
import yaml
import pandas as pd

PROCESSED_DIR = pathlib.Path("data/processed")
INPUT_PATH = PROCESSED_DIR / "report_card_district_milwaukee.csv"

def find_year_col(df):
    for candidate in ["SCHOOL_YEAR", "School Year", "School_Year"]:
        if candidate in df.columns:
            return candidate
    return None


VALUE_COL = "Overall Accountability Score"
DISTRICT_COL = "District Name"


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def run():
    if not INPUT_PATH.exists():
        print("[skip] report_card_district_milwaukee.csv not found — run fetch_report_cards.py first")
        return

    df = pd.read_csv(INPUT_PATH, low_memory=False)
    year_col = find_year_col(df)
    missing = [c for c in [VALUE_COL, DISTRICT_COL] if c not in df.columns]
    if not year_col:
        missing.append("a year column (SCHOOL_YEAR / School Year)")
    if missing:
        print(f"[error] report_card: missing expected columns {missing}. "
              f"Run: python inspect_report_card.py {INPUT_PATH}")
        return

    df[VALUE_COL] = pd.to_numeric(df[VALUE_COL], errors="coerce")

    cfg = load_config()
    threshold = cfg.get("flag_threshold_pct", 15)

    trend_rows, flag_rows = [], []

    for district, g in df.groupby(DISTRICT_COL):
        yearly = g.groupby(year_col)[VALUE_COL].mean().sort_index()
        yoy = yearly.diff()
        pct_change = yearly.pct_change() * 100
        label = f"{district} — All Students"

        for yr, val in yearly.items():
            trend_rows.append({
                "metric": "report_card",
                "group": label,
                "year": yr,
                "value": val,
                "yoy_change": yoy.get(yr),
                "pct_change": pct_change.get(yr),
            })
        for yr, pct in pct_change.items():
            if pd.notna(pct) and abs(pct) >= threshold:
                flag_rows.append({
                    "metric": "report_card",
                    "group": label,
                    "year": yr,
                    "value": yearly.get(yr),
                    "pct_change": round(pct, 1),
                })

    trend_df = pd.DataFrame(trend_rows)
    flag_df = pd.DataFrame(flag_rows)
    trend_df.to_csv(PROCESSED_DIR / "report_card_trend.csv", index=False)
    flag_df.to_csv(PROCESSED_DIR / "report_card_flags.csv", index=False)
    print(f"[ok] report_card: {len(trend_df)} trend rows, {len(flag_df)} flagged changes")

    all_flags_path = PROCESSED_DIR / "all_flags.csv"
    if all_flags_path.exists():
        existing = pd.read_csv(all_flags_path)
        existing = existing[existing["metric"] != "report_card"]
        combined = pd.concat([existing, flag_df], ignore_index=True)
    else:
        combined = flag_df
    combined.to_csv(all_flags_path, index=False)

    try:
        import analyze
        analyze.write_summary(combined)
    except Exception as e:
        print(f"[warn] could not regenerate SUMMARY.md: {e}")


if __name__ == "__main__":
    run()
