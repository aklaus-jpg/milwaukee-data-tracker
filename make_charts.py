"""
make_charts.py — draws a year-over-year line chart per metric into
charts/<metric>_trend.png. Trend data always contains every district and
demographic group DPI reports — this just controls which one(s) get charted.

Run directly:            python make_charts.py
Chart a specific group:  python make_charts.py "Rocketship Education Wisconsin Inc"
"""
import sys
import pathlib
import yaml
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROCESSED_DIR = pathlib.Path("data/processed")
CHART_DIR = pathlib.Path("charts")


def find_group(df, search_term):
    """Match a trend CSV's 'group' column against a search term. Labels look
    like 'Milwaukee — All Students' or 'Rocketship Education Wisconsin Inc —
    All Students' — search_term just needs to match part of that."""
    matches = df[df["group"].str.contains(search_term, case=False, na=False)]
    return matches


def make_chart(metric_name, search_term):
    path = PROCESSED_DIR / f"{metric_name}_trend.csv"
    if not path.exists():
        return

    df = pd.read_csv(path)
    matches = find_group(df, search_term)
    if matches.empty:
        print(f"[chart] {metric_name}: no group matching '{search_term}' found — skipped")
        return

    CHART_DIR.mkdir(exist_ok=True)
    plt.figure(figsize=(9, 5))
    for label, g_df in matches.groupby("group"):
        g_df = g_df.sort_values("year")
        plt.plot(g_df["year"].astype(str), g_df["value"], marker="o", label=label)

    plt.title(f"{metric_name.replace('_', ' ').title()} — matching '{search_term}'")
    plt.xlabel("School Year")
    plt.ylabel("Value")
    plt.xticks(rotation=45)
    plt.grid(alpha=0.3)
    if matches["group"].nunique() > 1:
        plt.legend(fontsize=8)
    plt.tight_layout()

    safe_name = search_term.replace(" ", "_").replace("/", "-")
    out_path = CHART_DIR / f"{metric_name}_{safe_name}_trend.png"
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"[chart] saved {out_path}")


def run_all(search_term="Milwaukee —"):
    # Note the em dash: "Milwaukee —" matches only the MPS row itself
    # ("Milwaukee — All Students"), not every charter with "Milwaukee"
    # somewhere in its name (e.g. "Milwaukee Math and Science Academy —
    # All Students" doesn't contain "Milwaukee —" as a substring).
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    for name in cfg["sources"]:
        make_chart(name, search_term)


if __name__ == "__main__":
    term = sys.argv[1] if len(sys.argv) > 1 else "Milwaukee —"
    run_all(term)
