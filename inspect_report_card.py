"""
inspect_report_card.py — like inspect_csv.py, but for the report card raw
files (which start life as .xlsx before being converted to .csv by
fetch_report_cards.py). Prints columns and sample rows so you can fix
find_county_col() or HEADER_ROW in fetch_report_cards.py if needed.

Run:  python inspect_report_card.py data/raw/report_card_school_raw.csv
"""
import sys
import pandas as pd


def inspect(path):
    df = pd.read_csv(path, nrows=200, low_memory=False)
    print(f"\n{path} — {df.shape[1]} columns, showing first 200 rows for sampling\n")
    print("Columns:")
    for c in df.columns:
        print(" -", c)
    print("\nSample rows:")
    print(df.head(5).to_string())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python inspect_report_card.py data/raw/<file>.csv")
        sys.exit(1)
    inspect(sys.argv[1])
