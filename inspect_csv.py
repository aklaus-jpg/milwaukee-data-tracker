"""
inspect_csv.py — quick helper to peek at a raw DPI CSV's columns and sample rows.

Use this the first time a source fails in analyze.py, to see the real column
names and fix config.yaml.

Run:  python inspect_csv.py data/raw/enrollment_raw.csv
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
        print("Usage: python inspect_csv.py data/raw/<file>.csv")
        sys.exit(1)
    inspect(sys.argv[1])
