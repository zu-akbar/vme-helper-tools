"""
Extract Render style from VME .ma files as a standalone module (no Styles or Render groups).

Produces the flattest possible hierarchy: Shell, Detail, Caps, CommonParts,
and global data (Overrides, Connectivity, Map_Data) live directly under the VME root.

Before:  VME_11003001 -> Styles -> Render -> {Shell, Detail, Caps, CommonParts}
After:   VME_11003001 -> {Shell, Detail, Caps, CommonParts, Overrides, Connectivity, Map_Data}

Run with:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/extract_render_manifestation_module.py [OPTIONS]

Examples:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/extract_render_manifestation_module.py
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/extract_render_manifestation_module.py --vme-ids VX0003001
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

logger = logging.getLogger("render_manifestation_module")

NON_RENDER_MARKERS = ("_Realtime_", "_BIPrint_")


def flatten_to_module(root):
    """Remove Styles and Render groups, promoting Render children directly under root."""
    import maya.cmds

    styles_path = f"|{root}|Styles"
    render_path = f"|{root}|Styles|Render"

    if not maya.cmds.objExists(render_path):
        raise RuntimeError(f"Render node not found: {render_path}")

    # Delete non-Render styles first
    styles_children = maya.cmds.listRelatives(styles_path, children=True, fullPath=True) or []
    for child in styles_children:
        if child.split("|")[-1] != "Render":
            maya.cmds.delete(child)

    # Get Render's children (Shell, Detail, Caps, CommonParts)
    render_children = maya.cmds.listRelatives(render_path, children=True, fullPath=True) or []

    # Reparent each Render child directly under root
    for child in render_children:
        maya.cmds.parent(child, root)

    # Delete the now-empty Render node
    if maya.cmds.objExists(render_path):
        maya.cmds.delete(render_path)

    # Delete the now-empty Styles node
    if maya.cmds.objExists(styles_path):
        maya.cmds.delete(styles_path)

    child_names = [c.split("|")[-1] for c in render_children]
    logger.debug(f"  Promoted to root: {', '.join(child_names)}")


def remove_non_render_sets():
    """Delete objectSets belonging to non-Render styles."""
    import maya.cmds

    all_sets = maya.cmds.ls(type="objectSet") or []
    for s in all_sets:
        if any(marker in s for marker in NON_RENDER_MARKERS):
            try:
                maya.cmds.lockNode(s, lock=False)
                maya.cmds.delete(s)
            except Exception:
                pass


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

    for node in unknown_nodes + unknown_dag:
        if node in connectivity_shapes:
            continue
        if maya.cmds.objExists(node):
            try:
                maya.cmds.lockNode(node, lock=False)
                maya.cmds.delete(node)
            except Exception:
                pass


def process_file(ma_path, output_path):
    """Open a VME .ma, flatten to module hierarchy, save."""
    import maya.cmds

    maya.cmds.file(ma_path, open=True, force=True, executeScriptNodes=False)

    roots = [t for t in maya.cmds.ls(assemblies=True) if t.startswith("VME_")]
    if not roots:
        raise RuntimeError("No VME root node found")

    root = roots[0]
    flatten_to_module(root)
    remove_non_render_sets()
    remove_unknown_nodes()

    maya.cmds.file(rename=output_path)
    maya.cmds.file(save=True, type="mayaAscii", executeScriptNodes=False)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract Render style as standalone module (no Styles/Render groups)",
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
        default=os.path.join(REPO_ROOT, "data", "output", "render-manifestation-module"),
        help="Output directory (default: data/output/render-manifestation-module)",
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
    )
    parser.add_argument("--log-file", metavar="PATH")
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
                logger.info(f"{prefix} - extracting module...")
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
