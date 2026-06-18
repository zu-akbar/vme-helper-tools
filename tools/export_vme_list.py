"""
Export a CSV listing all available VMEs with their revisions and type.

Run with:
    "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" export_vme_list.py [OPTIONS]

Or with Poetry (no Maya required):
    poetry run python tools/export_vme_list.py [OPTIONS]

Examples:
    # Export all VMEs from production
    python export_vme_list.py --env prod --output vme_list.csv

    # Export from dev environment
    python export_vme_list.py --env dev --output vme_dev.csv

    # Filter by SuperDesign
    python export_vme_list.py --super-designs 10000001 10000002
"""

import sys
import os
import argparse
import csv
import logging

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.paths import ensure_dep_paths_on_sys_path
ensure_dep_paths_on_sys_path(relative_to=REPO_ROOT)

from team_center.TeamCenter import RequestFailed
from team_center.TeamCenterFormats import revision_sort_key

from scripts.logging import setup_logging
from scripts.teamcenter import create_tc_session, paginate_search

logger = logging.getLogger("vme_export")


def fetch_all_vmes(session, page_size=50):
    """Retrieve all VME items from Team Center."""
    logger.info("Searching for all VMEs...")
    items = paginate_search(
        session,
        search_body={},
        page_size=page_size,
        query_elements={"ObjType": "VME"},
    )
    logger.info(f"Total VMEs found: {len(items)}")
    return items


def fetch_vmes_by_super_designs(session, super_design_ids, page_size=50):
    """Retrieve VMEs matching given SuperDesign IDs."""
    items = []
    for sd_id in super_design_ids:
        try:
            found = paginate_search(
                session,
                search_body={},
                page_size=page_size,
                query_elements={
                    "ObjType": "VME",
                    "SuperDesign": str(sd_id),
                },
            )
            logger.info(f"  SuperDesign {sd_id}: {len(found)} VME(s)")
            items.extend(found)
        except RequestFailed as e:
            logger.error(f"Failed to search for SuperDesign {sd_id}: {e}")
    return items


def build_vme_rows(items):
    """Build CSV rows from VME items. One row per revision."""
    rows = []
    for item in items:
        vme_id = item.get("ItemId", "unknown")
        vme_type = item.get("Type", item.get("ObjType", ""))
        name = item.get("Name", "")
        super_design = item.get("SuperDesign", "")

        allrevs = item.get("_ALLREVS", [])
        if allrevs:
            allrevs_sorted = sorted(allrevs, key=lambda r: revision_sort_key(r.get("Rev", "")))
            for rev_info in allrevs_sorted:
                rows.append({
                    "ItemId": vme_id,
                    "Name": name,
                    "Type": vme_type,
                    "SuperDesign": super_design,
                    "Revision": rev_info.get("Rev", ""),
                    "ReleaseStatus": rev_info.get("ReleaseStatus", ""),
                })
        else:
            rows.append({
                "ItemId": vme_id,
                "Name": name,
                "Type": vme_type,
                "SuperDesign": super_design,
                "Revision": item.get("Rev", ""),
                "ReleaseStatus": item.get("ReleaseStatus", ""),
            })

    return rows


def write_csv(rows, output_path):
    """Write rows to a CSV file."""
    fieldnames = ["ItemId", "Name", "Type", "SuperDesign", "Revision", "ReleaseStatus"]
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"Wrote {len(rows)} rows to {output_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export a CSV of all VMEs with their revisions and type",
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
        default=os.path.join(SCRIPT_DIR, "vme_list.csv"),
        help="Output CSV file path (default: tools/vme_list.csv)",
    )
    parser.add_argument(
        "--super-designs",
        nargs="+",
        metavar="ID",
        help="Filter VMEs by SuperDesign IDs",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="TC search page size (default: 50)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging verbosity (default: INFO)",
    )
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help="Also write log output to this file",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    setup_logging(args.log_level, args.log_file)

    session = create_tc_session(args.env)

    if args.super_designs:
        vme_items = fetch_vmes_by_super_designs(session, args.super_designs, args.page_size)
    else:
        vme_items = fetch_all_vmes(session, args.page_size)

    if not vme_items:
        logger.warning("No VME items found.")
        return

    rows = build_vme_rows(vme_items)
    write_csv(rows, args.output)

    unique_vmes = len(set(r["ItemId"] for r in rows))
    logger.info(f"Summary: {unique_vmes} VMEs, {len(rows)} total revisions")


if __name__ == "__main__":
    main()
