"""
run_pipeline.py — runs fetch -> analyze -> chart in order.
This is the single entry point the GitHub Action calls.

Run directly:  python run_pipeline.py
"""
import fetch_dpi
import analyze
import analyze_school_enrollment
import analyze_school_discipline
import analyze_school_discipline_groups
import analyze_school_movers
import fetch_report_cards
import analyze_report_cards
import make_charts


def main():
    cfg = fetch_dpi.load_config()
    fetch_dpi.fetch_all(cfg)
    analyze.run_all()
    analyze_school_enrollment.run()
    analyze_school_discipline.run()
    analyze_school_discipline_groups.run()
    analyze_school_movers.run()

    fetch_report_cards.run_all()
    analyze_report_cards.run()

    make_charts.run_all()


if __name__ == "__main__":
    main()
