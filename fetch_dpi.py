"""
fetch_dpi.py — downloads the raw DPI/WISEdash CSV files listed in config.yaml.

Run directly:  python fetch_dpi.py
"""
import pathlib
import yaml
import requests

RAW_DIR = pathlib.Path("data/raw")


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def fetch_all(config):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, src in config["sources"].items():
        url = src.get("url", "")
        if not url or url.startswith("PASTE"):
            print(f"[skip] {name}: no URL set in config.yaml yet")
            continue

        out_path = RAW_DIR / src["local_file"]
        print(f"[fetch] {name} -> {out_path}")
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            out_path.write_bytes(r.content)
        except requests.RequestException as e:
            print(f"[error] {name}: {e}")
    print("Done fetching.")


if __name__ == "__main__":
    cfg = load_config()
    fetch_all(cfg)
