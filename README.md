# Milwaukee Data Tracker

A small pipeline that pulls Milwaukee-area education data — starting with
MPS enrollment, chronic absenteeism, and suspensions/expulsions from DPI's
WISEdash public files — flags year-over-year changes above a threshold, and
produces CSVs + charts. Runs weekly on GitHub Actions so it works even when
your laptop's off.

Named "Milwaukee," not "MPS," on purpose: the structure is meant to grow
past MPS-only DPI data into county-level rollups, other local districts, and
Census ACS data down the line — all as new `sources:` entries in
`config.yaml`, not a rewrite.

**Update:** I checked DPI's live file listing directly, and their yearly ZIP
files follow a predictable naming pattern — so this now auto-builds download
URLs from a year list instead of requiring you to copy-paste links from the
WISEdash dropdown UI. Confirmed the exact prefixes for enrollment, chronic
absenteeism, and discipline actions against DPI's real page. Still budget
some time for first-run troubleshooting with `inspect_csv.py` (Step 2 below)
— column names inside the files can vary slightly by year, and I couldn't
open an actual file to verify since I have no live internet access in the
environment I built this in.

## What you get each run
- `data/raw/*.csv` — the raw DPI files as downloaded
- `data/processed/<metric>_trend.csv` — full year-by-year series for MPS, broken out by student group
- `data/processed/<metric>_flags.csv` — just the years/groups that crossed your threshold
- `data/processed/all_flags.csv` — everything flagged, combined
- `charts/<metric>_trend.png` — quick line chart, All Students
- `SUMMARY.md` — plain-English digest of what got flagged, at the repo root

## Setup

### 1. Push this to a GitHub repo
Create a new repo (public or private, either works) and push these files — you've already done this part.

### 2. First run + column-name troubleshooting
No URLs to hunt down — `config.yaml` already has real, working DPI file
prefixes and a list of school years. It builds each year's download URL
automatically (`https://dpi.wi.gov/sites/default/files/wise/downloads/<prefix>_<year>.zip`),
downloads it, unzips it, and stitches all years together.

Run it locally once before turning on the schedule:

```bash
pip install -r requirements.txt
python run_pipeline.py
```

If `analyze.py` prints something like `missing expected columns` or `no rows
matched district filter`, DPI's real column names differ slightly from what's
in `config.yaml` for that particular topic/year. Run:

```bash
python inspect_csv.py data/raw/enrollment_raw.csv
```

...and update the `*_col` values in `config.yaml` to match what's actually in
the file. Re-run until you see `[ok]` for all three sources with no warnings.

### 3. Turn on the schedule
Once local runs work cleanly, push any fixes to `config.yaml`. The GitHub
Action in `.github/workflows/weekly.yml` runs automatically every Monday, and
commits the updated data/charts/summary back into the repo. You can also
trigger it manually anytime from the **Actions** tab → **Weekly Milwaukee
Data Pull** → **Run workflow**.

No API keys or secrets are needed for this — WISEdash's public files don't
require authentication.

## Tuning it
- **`flag_threshold_pct`** in `config.yaml` — how big a year-over-year swing
  has to be before it lands in `SUMMARY.md`. Start at 5, tighten it once you
  see how noisy the data is.
- **`district_filter`** — matches on district name, defaults to `"Milwaukee"`
  which catches MPS. Change if you want a different district or a whole-county rollup.
- **`years`** — which school years to pull. Extend this list each fall once
  a new year's data is certified (usually posted by DPI in March for the
  prior year — check the file list before assuming a new year is live).
- **Adding more metrics** — find the exact file prefix at
  https://dpi.wi.gov/wisedash/public/download-files (hover the download link
  for the topic you want, confirm the `<prefix>_<year>.zip` pattern), add a
  new block under `sources:` in `config.yaml` with that `url_prefix`, then
  run `inspect_csv.py` on the resulting raw file to set the right column names.

## A newsroom note, not a coding one
Treat every number this spits out as a *lead*, not a publishable fact. DPI
data gets corrected via errata after publication, and WISEdash's own
disclaimer says derived analysis isn't attributable to DPI. Before you put a
flagged change in a story, pull the underlying number on the WISEdash Public
Portal itself and confirm it matches — this tool is for finding the story
fast, not for skipping verification.

## Possible next steps (not built yet)
- **Census ACS data** (child poverty, educational attainment by neighborhood)
  via the Census API — free key here: https://api.census.gov/data/key_signup.html.
  Useful for putting MPS numbers in city/neighborhood context. Ask me to add
  a `fetch_census.py` module when you're ready; it's a clean add-on to this
  same structure.
- School-level (not just district-level) breakdowns and maps.
- Posting `SUMMARY.md` flags to Slack/email automatically after each run.
