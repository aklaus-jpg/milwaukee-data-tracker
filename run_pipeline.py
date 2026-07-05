"""
run_pipeline.py — runs fetch -> analyze -> chart in order.
This is the single entry point the GitHub Action calls.

Run directly:  python run_pipeline.py
"""
import fetch_dpi
import analyze
import analyze_school_enrollment
import analyze_school_enrollment_groups
import analyze_school_discipline
import analyze_school_discipline_groups
import analyze_school_incidents
import analyze_school_absenteeism_groups
import analyze_school_movers
import analyze_school_act
import analyze_school_city
import analyze_school_forward
import fetch_report_cards
import analyze_report_cards
import make_charts


def main():
    cfg = fetch_dpi.load_config()
    fetch_dpi.fetch_all(cfg)
    analyze.run_all()
    analyze_school_enrollment.run()
    analyze_school_enrollment_groups.run()
    analyze_school_discipline.run()
    analyze_school_discipline_groups.run()
    analyze_school_incidents.run()
    analyze_school_absenteeism_groups.run()
    analyze_school_movers.run()

    # City tagging hits DPI's GIS portal (separate from WISEdash). If it's down,
    # keep the last school_city.csv rather than failing the whole weekly run.
    try:
        analyze_school_city.run()
    except Exception as e:
        print(f"[warn] school_city update skipped ({e}); keeping existing file")

    # Forward Exam fetches its own zips (two CSVs/year); keep last data on outage.
    try:
        analyze_school_forward.run()
    except Exception as e:
        print(f"[warn] forward update skipped ({e}); keeping existing files")

    # ACT (11th grade) fetches its own zips; same Forward-shape, keep data on outage.
    try:
        analyze_school_act.run()
    except Exception as e:
        print(f"[warn] act update skipped ({e}); keeping existing files")

    fetch_report_cards.run_all()
    analyze_report_cards.run()

    make_charts.run_all()


if __name__ == "__main__":
    main()
