"""
make_charts.py — draws a simple year-over-year line chart per metric (All Students)
into charts/<metric>_trend.png.

Run directly:  python make_charts.py
"""
import pathlib
import yaml
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROCESSED_DIR = pathlib.Path("data/processed")
CHART_DIR = pathlib.Path("charts")


def make_chart(metric_name):
    path = PROCESSED_DIR / f"{metric_name}_trend.csv"
    if not path.exists():
        return

    df = pd.read_csv(path)
    overall = df[df["group"] == "All Students"].sort_values("year")
    if overall.empty:
        return

    CHART_DIR.mkdir(exist_ok=True)
    plt.figure(figsize=(8, 5))
    plt.plot(overall["year"].astype(str), overall["value"], marker="o")
    plt.title(f"MPS {metric_name.replace('_', ' ').title()} — All Students")
    plt.xlabel("School Year")
    plt.ylabel("Value")
    plt.xticks(rotation=45)
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(CHART_DIR / f"{metric_name}_trend.png", dpi=150)
    plt.close()
    print(f"[chart] saved {metric_name}_trend.png")


def run_all():
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    for name in cfg["sources"]:
        make_chart(name)


if __name__ == "__main__":
    run_all()
