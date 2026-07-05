"""
archive_raw.py — provenance archive for fetched DPI raw files.

STANDING RULES for every importer (fetch_dpi, fetch_report_cards,
analyze_school_forward, and all future ones — ACT, HS completion, Tier 2, ...):

  1. Raw DPI files live in data/raw/, are gitignored, and are re-fetched on
     every run. The pipeline FETCHES then PROCESSES — it must never depend on a
     raw being committed to git.

  2. On each fetch, a dated, checksummed copy of every raw is written to
     data/archive/YYYY-MM-DD/ (also gitignored, outside git) — a timestamped
     record of exactly what DPI served on pull day, for provenance if a number
     is ever disputed.

To comply, any importer that writes a data/raw/*_raw.csv must call
`archive_raw(path)` immediately after saving it.
"""
import datetime
import hashlib
import pathlib
import shutil

ARCHIVE_DIR = pathlib.Path("data/archive")


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def archive_raw(path):
    """Copy a freshly-fetched raw into data/archive/<today>/ and record its
    sha256 in that day's SHA256SUMS.txt (idempotent per file per day)."""
    path = pathlib.Path(path)
    if not path.exists():
        print(f"  [archive] skip — {path} not found")
        return None

    day = datetime.date.today().isoformat()
    dest_dir = ARCHIVE_DIR / day
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest_dir / path.name)

    digest = _sha256(path)
    manifest = dest_dir / "SHA256SUMS.txt"
    # One line per filename; re-runs on the same day update in place rather than
    # accumulate duplicate rows. Format is `sha256  filename` (sha256sum -c).
    entries = {}
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            if "  " in line:
                d, name = line.split("  ", 1)
                entries[name] = d
    entries[path.name] = digest
    manifest.write_text("".join(f"{d}  {n}\n" for n, d in sorted(entries.items())))

    print(f"  [archived] {dest_dir / path.name} (sha256 {digest[:12]}…)")
    return digest
