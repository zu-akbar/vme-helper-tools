"""
Export a CSV listing all VMEs with their maturity tags — one row per revision.

Fetches the 3DFLOW report from Team Center which contains each VME revision
with its associated maturity tag.

Run with:
    poetry run python tools/export_vme_maturity.py --env prod
    poetry run python tools/export_vme_maturity.py --env prod --output maturity.csv

Or from an existing local CSV (no TC connection needed):
    poetry run python tools/export_vme_maturity.py --from-csv data/input/3DFLOW-list-type-active.csv
"""

import sys
import os
import argparse
import csv
import logging

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.paths import ensure_dep_paths_on_sys_path
ensure_dep_paths_on_sys_path(relative_to=REPO_ROOT)

from scripts.logging import setup_logging

logger = logging.getLogger("vme_maturity")


# ---------------------------------------------------------------------------
# Fetch from TC 3DFLOW report
# ---------------------------------------------------------------------------
def fetch_maturity_from_report(session):
    """Fetch the 3DFLOW VME report and extract maturity data."""
    from scripts.teamcenter import get_report_elements_by_type

    logger.info("Fetching 3DFLOW VME report from Team Center...")
    report = get_report_elements_by_type(session, "VME")

    rows = []
    if isinstance(report, list):
        items = report
    elif isinstance(report, dict):
        items = report.get("_DATA", report.get("data", []))
        if isinstance(items, dict):
            items = list(items.values())
    else:
        items = []

    for item in items:
        if isinstance(item, dict):
            rows.append({
                "TCID": item.get("TCID", item.get("ItemId", "")),
                "REV": item.get("REV", item.get("Rev", "")),
                "SUPERDESIGN": item.get("SUPERDESIGN", item.get("SuperDesign", "")),
                "MATURITYTAG": item.get("MATURITYTAG", item.get("MaturityTag", "")),
                "OWNINGUSER": item.get("OWNINGUSER", item.get("OwningUser", "")),
            })

    return rows


# ---------------------------------------------------------------------------
# Load from existing CSV
# ---------------------------------------------------------------------------
def load_from_csv(csv_path):
    """Load maturity data from an existing 3DFLOW CSV."""
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tcid = row.get("TCID", "").strip()
            if tcid:
                rows.append({
                    "TCID": tcid,
                    "REV": row.get("REV", "").strip(),
                    "SUPERDESIGN": row.get("SUPERDESIGN", "").strip(),
                    "MATURITYTAG": row.get("MATURITYTAG", "").strip(),
                    "OWNINGUSER": row.get("OWNINGUSER", "").strip(),
                })
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Export VME maturity tags — one row per (VME, revision, maturity tag)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--env",
        choices=["dev", "prod"],
        default="prod",
        help="Team Center environment (default: prod)",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(REPO_ROOT, "data", "output", "vme_maturity_tags.csv"),
        help="Output CSV path (default: data/output/vme_maturity_tags.csv)",
    )
    parser.add_argument(
        "--from-csv",
        metavar="PATH",
        help="Load from existing 3DFLOW CSV instead of fetching from TC",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument("--log-file", default=None)
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    args = parse_args()
    setup_logging(args.log_level, args.log_file, logger_name="vme_maturity")

    # --- Get data ---
    if args.from_csv:
        rows = load_from_csv(args.from_csv)
        logger.info(f"Loaded {len(rows)} rows from {args.from_csv}")
    else:
        from scripts.teamcenter import create_tc_session
        session = create_tc_session(args.env)
        rows = fetch_maturity_from_report(session)
        logger.info(f"Fetched {len(rows)} rows from TC report")

    if not rows:
        logger.warning("No data found.")
        return

    # --- Sort: by TCID, then by maturity tag ---
    rows.sort(key=lambda r: (r["TCID"], r["MATURITYTAG"], r["REV"]))

    # --- Write output CSV ---
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    fieldnames = ["TCID", "REV", "SUPERDESIGN", "MATURITYTAG", "OWNINGUSER"]

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # --- Summary ---
    unique_vmes = len(set(r["TCID"] for r in rows))
    tags = {}
    for r in rows:
        tag = r["MATURITYTAG"] or "(empty)"
        tags[tag] = tags.get(tag, 0) + 1

    logger.info(f"Written {len(rows)} rows to {args.output}")
    logger.info(f"  Unique VMEs: {unique_vmes}")
    logger.info(f"  Maturity tag distribution:")
    for tag, count in sorted(tags.items(), key=lambda x: -x[1]):
        logger.info(f"    {tag}: {count}")


if __name__ == "__main__":
    main()
