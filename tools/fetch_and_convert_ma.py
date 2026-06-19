"""
Batch download VME .mb files from Team Center and convert to .ma (Maya ASCII).

Run with:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/fetch_and_convert_ma.py [OPTIONS]

Examples:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/fetch_and_convert_ma.py --env prod --output-dir ./output
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/fetch_and_convert_ma.py --vme-ids VX0003001 VX0003002
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/fetch_and_convert_ma.py --super-designs 10000001 10000002
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/fetch_and_convert_ma.py --dry-run
"""

import sys
import os
import argparse
import logging
import tempfile

# ---------------------------------------------------------------------------
# Path setup: make shared package and corporate deps importable
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.paths import ensure_dep_paths_on_sys_path
ensure_dep_paths_on_sys_path(relative_to=REPO_ROOT)

from team_center.TeamCenter import RequestFailed

from scripts.logging import setup_logging
from scripts.teamcenter import create_tc_session
from scripts.vme import (
    fetch_all_vmes,
    fetch_vmes_by_ids,
    fetch_vmes_by_super_designs,
    get_final_revision,
    download_vme_mb,
)
from scripts.maya_convert import initialize_maya, uninitialize_maya, convert_mb_to_ma

logger = logging.getLogger("vme_batch")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Batch download VME files from Team Center and convert .mb to .ma",
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
        "--output-dir",
        default=os.path.join(REPO_ROOT, "data", "output"),
        help="Output directory for .ma files (default: data/output)",
    )
    parser.add_argument(
        "--temp-dir",
        default=os.path.join(tempfile.gettempdir(), "DWF", "vme_batch"),
        help="Temp directory for .mb downloads (default: %%TEMP%%/DWF/vme_batch)",
    )
    parser.add_argument(
        "--vme-ids",
        nargs="+",
        metavar="ID",
        help="Process only these VME IDs (e.g., VX0003001 VX0003002)",
    )
    parser.add_argument(
        "--super-designs",
        nargs="+",
        metavar="ID",
        help="Process VMEs matching these SuperDesign IDs",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="TC search page size (default: 50)",
    )
    parser.add_argument(
        "--keep-mb",
        action="store_true",
        help="Keep intermediate .mb files after conversion",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List VMEs and their final revisions without downloading",
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

    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(args.temp_dir, exist_ok=True)

    # --- Authenticate and enumerate VMEs ---
    session = create_tc_session(args.env)

    if args.vme_ids:
        vme_items = fetch_vmes_by_ids(session, args.vme_ids)
    elif args.super_designs:
        vme_items = fetch_vmes_by_super_designs(
            session, args.super_designs, args.page_size
        )
    else:
        vme_items = fetch_all_vmes(session, args.page_size)

    if not vme_items:
        logger.warning("No VME items found. Exiting.")
        return

    # --- Determine final revision for each VME ---
    work_list = []
    for item in vme_items:
        vme_id = item.get("ItemId", "unknown")
        final_rev = get_final_revision(item)
        if final_rev:
            work_list.append((vme_id, final_rev))
        else:
            logger.warning(f"  {vme_id}: no released revision found, skipping.")

    logger.info(f"VMEs with a final revision: {len(work_list)}")

    if args.dry_run:
        logger.info("--- DRY RUN: listing VMEs ---")
        for vme_id, rev in work_list:
            print(f"  {vme_id}  rev={rev}")
        logger.info("Done (dry run).")
        return

    # --- Initialize Maya and convert ---
    initialize_maya()

    success_count = 0
    fail_count = 0
    failures = []

    try:
        for idx, (vme_id, rev) in enumerate(work_list, start=1):
            prefix = f"[{idx}/{len(work_list)}] {vme_id} rev {rev}"
            try:
                logger.info(f"{prefix} - downloading...")
                mb_path = download_vme_mb(session, vme_id, rev, args.temp_dir)

                ma_filename = f"{vme_id}_{rev}.ma"
                ma_path = os.path.join(args.output_dir, ma_filename)
                logger.info(f"{prefix} - converting to .ma...")
                convert_mb_to_ma(mb_path, ma_path)

                if not args.keep_mb:
                    os.remove(mb_path)

                logger.info(f"{prefix} - DONE -> {ma_filename}")
                success_count += 1

            except RequestFailed as e:
                logger.error(f"{prefix} - TC download failed: {e}")
                fail_count += 1
                failures.append((vme_id, rev, str(e)))

            except Exception as e:
                logger.error(f"{prefix} - failed: {e}")
                fail_count += 1
                failures.append((vme_id, rev, str(e)))

    finally:
        uninitialize_maya()

    # --- Summary ---
    logger.info("=" * 60)
    logger.info(f"Completed: {success_count} success, {fail_count} failed")
    if failures:
        logger.info("Failed items:")
        for vme_id, rev, error in failures:
            logger.info(f"  {vme_id} rev {rev}: {error}")
    logger.info(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
