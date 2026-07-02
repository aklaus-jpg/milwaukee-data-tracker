"""
run_pipeline.py — runs fetch -> analyze -> chart in order.
This is the single entry point the GitHub Action calls.

Run directly:  python run_pipeline.py
"""
import fetch_dpi
import analyze
import make_charts


def main():
    cfg = fetch_dpi.load_config()
    fetch_dpi.fetch_all(cfg)
    analyze.run_all()
    make_charts.run_all()


if __name__ == "__main__":
    main()
