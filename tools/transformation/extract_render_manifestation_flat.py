"""
Extract Render style from VME .ma files with a flattened hierarchy (no Styles node).

Produces a cleaner scene structure where the Render node lives directly under
the VME root, removing the unnecessary Styles wrapper.

Before:  VME_11003001 → Styles → Render → {Shell, Detail, Caps, CommonParts}
After:   VME_11003001 → Render → {Shell, Detail, Caps, CommonParts}

Run with:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/extract_render_flat.py [OPTIONS]

Examples:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/extract_render_flat.py
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/extract_render_flat.py --vme-ids VX0003001
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

logger = logging.getLogger("render_manifestation_flat")

NON_RENDER_MARKERS = ("_Realtime_", "_BIPrint_")


def flatten_styles_node(root):
    """Remove the Styles grouping node, reparenting Render directly under root."""
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

    # Reparent Render directly under root (removes Styles wrapper)
    maya.cmds.parent(render_path, root)

    # Delete the now-empty Styles node
    if maya.cmds.objExists(styles_path):
        maya.cmds.delete(styles_path)


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
    """Open a VME .ma, flatten Render hierarchy, save."""
    import maya.cmds

    maya.cmds.file(ma_path, open=True, force=True, executeScriptNodes=False)

    roots = [t for t in maya.cmds.ls(assemblies=True) if t.startswith("VME_")]
    if not roots:
        raise RuntimeError("No VME root node found")

    root = roots[0]
    flatten_styles_node(root)
    remove_non_render_sets()
    remove_unknown_nodes()

    maya.cmds.file(rename=output_path)
    maya.cmds.file(save=True, type="mayaAscii", executeScriptNodes=False)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract Render style with flattened hierarchy (no Styles node)",
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
        default=os.path.join(REPO_ROOT, "data", "output", "render-manifestation-flat"),
        help="Output directory (default: data/output/render-manifestation-flat)",
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
                logger.info(f"{prefix} - flattening...")
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
