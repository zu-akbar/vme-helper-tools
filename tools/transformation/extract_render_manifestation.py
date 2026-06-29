"""
Extract the Render style from VME .ma files into standalone .ma files.

Removes non-Render styles (Realtime, BIPrint) and their associated objectSets,
keeping only the Render geometry, global data, and Render-related sets.

Run with:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/extract_render_manifestation.py [OPTIONS]

Examples:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/extract_render_manifestation.py
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/extract_render_manifestation.py --input-dir data/output/VME
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/extract_render_manifestation.py --vme-ids VX0003001 VX0049097
"""

import sys
import os
import argparse
import logging
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.logging import setup_logging
from scripts.maya_convert import initialize_maya, uninitialize_maya

logger = logging.getLogger("render_extract")

NON_RENDER_STYLES = ("Realtime", "BIPrint")
NON_RENDER_SET_MARKERS = ("_Realtime_", "_BIPrint_")


def extract_render_style():
    """Remove non-Render styles and their sets from the current scene."""
    import maya.cmds

    roots = [t for t in maya.cmds.ls(assemblies=True) if t.startswith("VME_")]
    if not roots:
        raise RuntimeError("No VME root node found in scene")

    root = roots[0]
    styles_path = f"|{root}|Styles"

    if not maya.cmds.objExists(styles_path):
        raise RuntimeError(f"Styles node not found: {styles_path}")

    children = maya.cmds.listRelatives(styles_path, children=True, fullPath=True) or []
    deleted_styles = []
    for child in children:
        name = child.split("|")[-1]
        if name != "Render":
            maya.cmds.delete(child)
            deleted_styles.append(name)

    if deleted_styles:
        logger.debug(f"  Deleted styles: {', '.join(deleted_styles)}")

    all_sets = maya.cmds.ls(type="objectSet") or []
    deleted_sets = 0
    for s in all_sets:
        if any(marker in s for marker in NON_RENDER_SET_MARKERS):
            try:
                maya.cmds.lockNode(s, lock=False)
                maya.cmds.delete(s)
                deleted_sets += 1
            except Exception:
                pass

    if deleted_sets:
        logger.debug(f"  Deleted {deleted_sets} non-Render objectSets")


def remove_unknown_nodes():
    """Remove unknown nodes that block .ma saving, preserving connectivity shapes."""
    import maya.cmds

    unknown_nodes = maya.cmds.ls(type="unknown") or []
    unknown_dag = maya.cmds.ls(type="unknownDag") or []

    connectivity_shapes = set()
    for node in unknown_dag:
        if not maya.cmds.objExists(node):
            continue
        full_path = maya.cmds.ls(node, long=True)
        if full_path and "|Connectivity|" in full_path[0]:
            connectivity_shapes.add(node)

    to_delete = [n for n in unknown_nodes + unknown_dag if n not in connectivity_shapes]
    if to_delete:
        logger.debug(f"  Removing {len(to_delete)} unknown node(s) (keeping {len(connectivity_shapes)} connectivity shape(s))...")
        for node in to_delete:
            if maya.cmds.objExists(node):
                try:
                    maya.cmds.lockNode(node, lock=False)
                    maya.cmds.delete(node)
                except Exception:
                    pass


def process_file(ma_path, output_path):
    """Open a .ma file, extract Render style, save to output."""
    import maya.cmds

    maya.cmds.file(ma_path, open=True, force=True, executeScriptNodes=False)
    extract_render_style()
    remove_unknown_nodes()
    maya.cmds.file(rename=output_path)
    maya.cmds.file(save=True, type="mayaAscii", executeScriptNodes=False)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract Render manifestation from VME .ma files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--input-dir",
        default=os.path.join(REPO_ROOT, "data", "output", "VME"),
        help="Directory containing source .ma files (default: data/output/VME)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(REPO_ROOT, "data", "output", "Render-Manifestation"),
        help="Output directory (default: data/output/Render-Manifestation)",
    )
    parser.add_argument(
        "--vme-ids",
        nargs="+",
        metavar="ID",
        help="Only process files matching these VME IDs",
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

    if not os.path.isdir(args.input_dir):
        logger.error(f"Input directory not found: {args.input_dir}")
        return

    os.makedirs(args.output_dir, exist_ok=True)

    ma_files = sorted(glob.glob(os.path.join(args.input_dir, "*.ma")))
    if args.vme_ids:
        ids = set(args.vme_ids)
        ma_files = [f for f in ma_files if any(vid in os.path.basename(f) for vid in ids)]

    if not ma_files:
        logger.warning("No .ma files to process.")
        return

    logger.info(f"Processing {len(ma_files)} file(s) from {args.input_dir}")

    initialize_maya()

    success_count = 0
    fail_count = 0

    try:
        for idx, ma_path in enumerate(ma_files, start=1):
            filename = os.path.basename(ma_path)
            output_path = os.path.join(args.output_dir, filename)
            prefix = f"[{idx}/{len(ma_files)}] {filename}"

            try:
                logger.info(f"{prefix} - extracting Render...")
                process_file(ma_path, output_path)
                logger.info(f"{prefix} - DONE")
                success_count += 1
            except Exception as e:
                logger.error(f"{prefix} - failed: {e}")
                fail_count += 1
    finally:
        uninitialize_maya()

    logger.info("=" * 60)
    logger.info(f"Completed: {success_count} success, {fail_count} failed")
    logger.info(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
