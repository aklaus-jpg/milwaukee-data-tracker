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

**Honest caveat:** this was built without live access to test the DPI
download links, since DPI's file page is a dropdown-driven tool rather than a
stable API. The pipeline is built to fail loudly and safely (skip a source,
print a clear error) rather than silently produce garbage — but budget 15–20
minutes for first-run troubleshooting with `inspect_csv.py` (see Step 3).

## What you get each run
- `data/raw/*.csv` — the raw DPI files as downloaded
- `data/processed/<metric>_trend.csv` — full year-by-year series for MPS, broken out by student group
- `data/processed/<metric>_flags.csv` — just the years/groups that crossed your threshold
- `data/processed/all_flags.csv` — everything flagged, combined
- `charts/<metric>_trend.png` — quick line chart, All Students
- `SUMMARY.md` — plain-English digest of what got flagged, at the repo root

## Setup

### 1. Push this to a GitHub repo
Create a new repo (public or private, either works) and push these files.

### 2. Get the DPI CSV URLs
For each of the three sources — **Enrollment**, **Attendance** (for chronic
absenteeism), and **Discipline** (for suspensions/expulsions):

1. Go to https://dpi.wi.gov/wisedash/download-files
2. Pick the topic and school year (or "All Years"/"Multiple Years" if offered — better, since you want a time series)
3. Right-click the CSV download link/button → **Copy Link Address**
4. Paste it into `config.yaml` under that source's `url:` field

Commit the updated `config.yaml`.

### 3. First run + column-name troubleshooting
Run it locally once before turning on the schedule:

```bash
pip install -r requirements.txt
python run_pipeline.py
```

If `analyze.py` prints something like `missing expected columns` or `no rows
matched district filter`, DPI's real column names differ slightly from what's
in `config.yaml`. Run:

```bash
python inspect_csv.py data/raw/enrollment_raw.csv
```

...and update the `*_col` values in `config.yaml` to match what's actually in
the file (e.g., DPI sometimes uses `DISTRICT` instead of `DISTRICT_NAME`, or
`GROUP_BY_VALUE_TEXT` instead of `GROUP_BY_VALUE` — this varies by topic and
has changed across years). Re-run until you see `[ok]` for all three sources.

### 4. Turn on the schedule
Once local runs work cleanly, push your fixed `config.yaml`. The GitHub Action
in `.github/workflows/weekly.yml` will run automatically every Monday, and
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
- **Adding more metrics** — copy one of the blocks under `sources:` in
  `config.yaml`, point it at a new WISEdash topic (e.g., graduation rate,
  test scores), and re-run `inspect_csv.py` on the new file to set the right
  column names.

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
