"""
Extract Render style from VME .ma files as a simplified standalone module.

Target hierarchy:
  VME_Geometries/{Shell, Detail, Caps}
  CommonParts/{Knobs, Tubes, Logo}
  Overrides
  Map_Data/Normal/Source/Render_Without_Airgap
  Rig/Skeleton
  VME_Sets/{EdgeSets, CommonPartSets, SmartSectionSets, FaceSets, OverrideSectionSets}

Original VME identity (SuperDesign, ID, Revision, Style) stored as fileInfo metadata.

Run with:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/transformation/extract_render_manifestation_module.py [OPTIONS]

Examples:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/transformation/extract_render_manifestation_module.py
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/transformation/extract_render_manifestation_module.py --vme-ids VX0003001
"""

import sys
import os
import argparse
import logging
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.logging import setup_logging
from scripts.maya_convert import initialize_maya, uninitialize_maya

logger = logging.getLogger("render_manifestation_module")

NON_RENDER_MARKERS = ("_Realtime_", "_BIPrint_")


def flatten_to_module(root):
    """Remove Styles/Render wrappers and collapse the Shell intermediate transform."""
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

    # Collapse Shell: VME/Shell/Shell/ShellShape -> VME/Shell/ShellShape
    collapse_shell(root)

    child_names = [c.split("|")[-1] for c in render_children]
    logger.debug(f"  Promoted to root: {', '.join(child_names)}")


def collapse_shell(root):
    """Collapse the intermediate Shell transform: Shell/Shell/ShellShape -> Shell/ShellShape."""
    import maya.cmds

    shell_group = f"|{root}|Shell"
    if not maya.cmds.objExists(shell_group):
        return

    inner_shell = f"|{root}|Shell|Shell"
    if not maya.cmds.objExists(inner_shell):
        return

    # Transfer custom attributes from inner Shell to outer Shell
    user_attrs = maya.cmds.listAttr(inner_shell, userDefined=True) or []
    for attr in user_attrs:
        attr_type = maya.cmds.getAttr(f"{inner_shell}.{attr}", type=True)
        value = maya.cmds.getAttr(f"{inner_shell}.{attr}")
        if not maya.cmds.attributeQuery(attr, node=shell_group, exists=True):
            if attr_type == "string":
                maya.cmds.addAttr(shell_group, longName=attr, dataType="string")
                maya.cmds.setAttr(f"{shell_group}.{attr}", value, type="string")
            else:
                maya.cmds.addAttr(shell_group, longName=attr, attributeType=attr_type)
                maya.cmds.setAttr(f"{shell_group}.{attr}", value)

    # Transfer .iog connections (objectSet memberships) from inner Shell to outer Shell
    iog_connections = maya.cmds.listConnections(f"{inner_shell}.iog", source=False, destination=True, plugs=True) or []
    for dest_plug in iog_connections:
        maya.cmds.connectAttr(f"{shell_group}.iog", dest_plug, force=True)

    # Reparent shapes under the inner Shell up to the outer Shell group
    inner_shapes = maya.cmds.listRelatives(inner_shell, shapes=True, fullPath=True) or []
    for shape in inner_shapes:
        maya.cmds.parent(shape, shell_group, relative=True, shape=True)

    # Reparent any remaining transform children
    inner_transforms = maya.cmds.listRelatives(inner_shell, children=True, type="transform", fullPath=True) or []
    for xform in inner_transforms:
        maya.cmds.parent(xform, shell_group)

    # Delete the now-empty inner Shell transform
    if maya.cmds.objExists(inner_shell):
        maya.cmds.delete(inner_shell)

    logger.debug(f"  Collapsed Shell hierarchy")


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



def restructure_hierarchy(root, filename):
    """Reorganize into target hierarchy: VME_Geometries, CommonParts, Overrides, Map_Data, Rig, VME_Sets."""
    import maya.cmds

    super_design = root.replace("VME_", "", 1)

    # Store original VME identity as global fileInfo metadata
    vme_id = filename.rsplit("_", 1)[0] if "_" in filename else filename
    revision = filename.rsplit("_", 1)[1] if "_" in filename else ""
    maya.cmds.fileInfo("VME_SuperDesign", super_design)
    maya.cmds.fileInfo("VME_ID", vme_id)
    maya.cmds.fileInfo("VME_Revision", revision)
    maya.cmds.fileInfo("VME_Style", "Render")

    # Create VME_Geometries group and move geometry children into it
    vme_geo = maya.cmds.createNode("transform", name="VME_Geometries", parent=root)
    for child_name in ("Shell", "Detail", "Caps"):
        child_path = f"|{root}|{child_name}"
        if maya.cmds.objExists(child_path):
            maya.cmds.parent(child_path, vme_geo)

    # Move CommonParts to root level (sibling to VME_Geometries)
    # It's already under root from flatten_to_module, nothing to do

    # Remove Connectivity (not part of target hierarchy)
    conn_path = f"|{root}|Connectivity"
    if maya.cmds.objExists(conn_path):
        maya.cmds.delete(conn_path)

    # Rename VME_Data -> VME_Sets
    vme_data_name = f"VME_Data_{super_design}"
    if maya.cmds.objExists(vme_data_name):
        maya.cmds.rename(vme_data_name, "VME_Sets")


    # Rename the VME root (remove SuperDesign) — do this last since paths change
    maya.cmds.rename(root, "VME")

    # Enforce child ordering under VME root
    desired_order = ["VME_Geometries", "CommonParts", "Rig", "Map_Data", "Overrides"]
    for name in reversed(desired_order):
        path = f"|VME|{name}"
        if maya.cmds.objExists(path):
            maya.cmds.reorder(path, front=True)

    logger.debug(f"  Restructured hierarchy, stored metadata: SD={super_design} ID={vme_id} Rev={revision}")
    return "VME"


def process_file(ma_path, output_path):
    """Open a VME .ma, flatten to module hierarchy, save."""
    import maya.cmds

    maya.cmds.file(ma_path, open=True, force=True, executeScriptNodes=False)

    roots = [t for t in maya.cmds.ls(assemblies=True) if t.startswith("VME_")]
    if not roots:
        raise RuntimeError("No VME root node found")

    root = roots[0]
    filename = os.path.splitext(os.path.basename(ma_path))[0]
    flatten_to_module(root)
    remove_non_render_sets()
    remove_unknown_nodes()
    restructure_hierarchy(root, filename)

    maya.cmds.file(rename=output_path)
    maya.cmds.file(save=True, type="mayaAscii", executeScriptNodes=False)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract Render style as simplified module (collapsed Shell, no Styles/Render groups)",
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
