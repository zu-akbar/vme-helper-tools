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
from team_center.TeamCenterFormats import revision_sort_key

from scripts.logging import setup_logging
from scripts.teamcenter import create_tc_session, paginate_search

logger = logging.getLogger("vme_batch")


# ---------------------------------------------------------------------------
# VME Enumeration
# ---------------------------------------------------------------------------
def fetch_all_vmes(session, page_size=50):
    """Retrieve all VME items from Team Center with pagination."""
    logger.info("Searching for all VMEs in Team Center...")
    items = paginate_search(
        session,
        search_body={},
        page_size=page_size,
        query_elements={"ObjType": "VME"},
    )
    logger.info(f"Total VMEs found: {len(items)}")
    return items


def fetch_vmes_by_ids(session, vme_ids):
    """Retrieve specific VME items by their IDs."""
    items = []
    for vme_id in vme_ids:
        try:
            response = session.get_item(
                tcid=vme_id, query_elements={"allrevs": None}
            )
            item_data = response.get("_DATA", {})
            if isinstance(item_data, dict) and vme_id in item_data:
                rev_key = next(iter(item_data[vme_id]))
                item = item_data[vme_id][rev_key]
                item["ItemId"] = vme_id
                if "_ALLREVS" not in item and "_ALLREVS" in response:
                    item["_ALLREVS"] = response["_ALLREVS"]
                items.append(item)
            elif isinstance(item_data, list):
                items.extend(item_data)
            else:
                items.append(response)
        except RequestFailed as e:
            logger.error(f"Failed to fetch VME {vme_id}: {e}")
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


# ---------------------------------------------------------------------------
# Revision logic
# ---------------------------------------------------------------------------
def get_final_revision(item):
    """Determine the final (latest released) revision of a VME item.

    Returns the revision string (e.g., "B") or None if no released revision exists.
    """
    allrevs = item.get("_ALLREVS", [])
    if not allrevs:
        rev = item.get("Rev")
        status = item.get("ReleaseStatus", "")
        if rev and status != "Working":
            return rev
        return None

    released = [r for r in allrevs if r.get("ReleaseStatus") != "Working"]
    if not released:
        return None

    released.sort(key=lambda r: revision_sort_key(r["Rev"]), reverse=True)
    return released[0]["Rev"]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def download_vme_mb(session, vme_id, rev, temp_dir):
    """Download the .mb file for a VME from Team Center."""
    uri = f"/items/{vme_id}/specifications/mb/vme.mb"
    if rev:
        uri += f"?Rev={rev}"

    output_file = os.path.join(temp_dir, f"{vme_id}_{rev}.mb")
    session.download_file(uri, output_file)
    return output_file


# ---------------------------------------------------------------------------
# Maya conversion
# ---------------------------------------------------------------------------
def initialize_maya():
    """Initialize Maya in standalone (headless) mode."""
    import maya.standalone
    maya.standalone.initialize(name="vme_batch")
    logger.info("Maya standalone initialized.")


def uninitialize_maya():
    """Shut down Maya standalone."""
    try:
        import maya.standalone
        maya.standalone.uninitialize()
    except Exception:
        pass


def convert_mb_to_ma(mb_path, ma_path):
    """Open a .mb file in Maya and save it as .ma (Maya ASCII)."""
    import maya.cmds
    maya.cmds.file(mb_path, open=True, force=True, executeScriptNodes=False)
    maya.cmds.file(rename=ma_path)
    maya.cmds.file(save=True, type="mayaAscii", executeScriptNodes=False)


# ---------------------------------------------------------------------------
# Main
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
        default=os.path.join(SCRIPT_DIR, "output"),
        help="Output directory for .ma files (default: ./output)",
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

    # --- Phase 1: Authenticate and enumerate VMEs ---
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

    # --- Phase 2: Determine final revision for each VME ---
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

    # --- Phase 3: Initialize Maya ---
    initialize_maya()

    # --- Phase 4: Download and convert each VME ---
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

    # --- Phase 5: Summary ---
    logger.info("=" * 60)
    logger.info(f"Completed: {success_count} success, {fail_count} failed")
    if failures:
        logger.info("Failed items:")
        for vme_id, rev, error in failures:
            logger.info(f"  {vme_id} rev {rev}: {error}")
    logger.info(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
