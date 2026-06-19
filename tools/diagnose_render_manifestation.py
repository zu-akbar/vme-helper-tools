"""
Diagnose Render Manifestation .ma files and run export-style (Render) extraction.

Replicates the ved_tool export-style procedure:
1. Duplicates Render geometry
2. Combines Shell + Detail into a single merged mesh
3. Renames result to m{design_id} (e.g., m3001 for VME_11003001)
4. Reparents CommonParts (Knobs, Tubes, Pins) as flat children
5. Bakes crease sets, deletes construction history
6. Exports as standalone .ma file

Run with:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/diagnose_render_manifestation.py [OPTIONS]

Examples:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/diagnose_render_manifestation.py
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/diagnose_render_manifestation.py --vme-ids VX0003001
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/diagnose_render_manifestation.py --skip-export
"""

import sys
import os
import argparse
import logging
import glob
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.logging import setup_logging
from scripts.maya_convert import initialize_maya, uninitialize_maya

logger = logging.getLogger("render_diag")

EXPECTED_RENDER_GROUPS = {"Shell", "Detail", "Caps", "CommonParts"}
EXPECTED_GLOBAL_GROUPS = {"Styles", "Overrides", "Connectivity", "Map_Data"}
REQUIRED_SETS = {"_Render_BI", "_Render_Black_faceset", "_Render_SmartSection_Shell"}
NON_RENDER_MARKERS = ("_Realtime_", "_BIPrint_")


def get_preview_name(root):
    """Derive the export name from the VME root (ved_tool convention: m{design_id})."""
    export_name = root.replace("VME_", "", 1)
    if export_name and export_name[0].isdigit():
        export_name = "m" + export_name
    return export_name


def get_all_geom_nodes(root):
    """Get transform parents of all mesh nodes under root."""
    import maya.cmds
    all_nodes = []
    geom_nodes = maya.cmds.ls(root, dag=True, long=True, type="mesh")
    if geom_nodes:
        for geom in geom_nodes:
            parent = maya.cmds.listRelatives(geom, parent=True, fullPath=True)
            if parent and parent[0] not in all_nodes:
                all_nodes.append(parent[0])
    return all_nodes


def get_node_in_root(root, child_name):
    """Find a direct or nested child with name under root."""
    import maya.cmds
    matches = maya.cmds.ls(f"{root}|*|{child_name}", long=True) or maya.cmds.ls(f"{root}|{child_name}", long=True)
    if matches:
        return matches[0]
    # Try deeper search
    all_children = maya.cmds.listRelatives(root, allDescendents=True, fullPath=True, type="transform") or []
    for c in all_children:
        if c.split("|")[-1] == child_name:
            return c
    return None


def diagnose_file(ma_path):
    """Open a Render Manifestation .ma and return diagnostic data."""
    import maya.cmds

    maya.cmds.file(ma_path, open=True, force=True, executeScriptNodes=False)

    result = {
        "file": os.path.basename(ma_path),
        "size_kb": round(os.path.getsize(ma_path) / 1024),
        "issues": [],
    }

    roots = [t for t in maya.cmds.ls(assemblies=True) if t.startswith("VME_")]
    if not roots:
        result["issues"].append("CRITICAL: No VME root node found")
        result["status"] = "FAIL"
        return result

    root = roots[0]
    result["root"] = root
    result["preview_name"] = get_preview_name(root)

    styles_path = f"|{root}|Styles"
    if not maya.cmds.objExists(styles_path):
        result["issues"].append("CRITICAL: No Styles group found")
        result["status"] = "FAIL"
        return result

    styles_children = maya.cmds.listRelatives(styles_path, children=True) or []
    result["styles_children"] = styles_children

    if "Render" not in styles_children:
        result["issues"].append("CRITICAL: Render style missing")
    for child in styles_children:
        if child != "Render":
            result["issues"].append(f"ERROR: Non-Render style present: {child}")

    render_path = f"|{root}|Styles|Render"
    render_children = set(maya.cmds.listRelatives(render_path, children=True) or [])
    result["render_groups"] = sorted(render_children)

    missing_groups = EXPECTED_RENDER_GROUPS - render_children
    if missing_groups:
        result["issues"].append(f"WARNING: Missing Render subgroups: {sorted(missing_groups)}")

    all_meshes = maya.cmds.ls(type="mesh", long=True) or []
    render_meshes = [m for m in all_meshes if "|Render|" in m]
    result["mesh_count_render"] = len(render_meshes)
    result["mesh_count_total"] = len(all_meshes)

    if len(render_meshes) == 0:
        result["issues"].append("WARNING: No mesh nodes found under Render path")

    root_children = set(maya.cmds.listRelatives(root, children=True) or [])
    result["root_groups"] = sorted(root_children)

    all_sets = maya.cmds.ls(type="objectSet") or []
    render_sets = [s for s in all_sets if "_Render_" in s]
    leaked_sets = [s for s in all_sets if any(m in s for m in NON_RENDER_MARKERS)]

    result["render_sets"] = render_sets
    result["leaked_sets"] = leaked_sets

    if leaked_sets:
        result["issues"].append(f"ERROR: Non-Render sets still present: {leaked_sets}")

    for req in REQUIRED_SETS:
        if not any(req in s for s in render_sets):
            result["issues"].append(f"WARNING: Required set pattern '{req}' not found")

    result["status"] = "PASS" if not any("CRITICAL" in i or "ERROR" in i for i in result["issues"]) else "FAIL"
    return result


def setup_color_change_uvsets(shape, root):
    """Create Exp_ColorChange UV sets on the shell mesh (replicates ved_tool UVSetsUtility).

    Reads LEGO_CHANNEL_* attributes from the VME root to determine which UV sets to create.
    """
    import maya.cmds

    attributes = maya.cmds.listAttr(root, userDefined=True) or []
    channels = {}
    for att in attributes:
        if "LEGO_CHANNEL_" in att:
            channels[att] = maya.cmds.getAttr(f"{root}.{att}")

    if not channels:
        return

    for key, value in channels.items():
        if "Default_Color" in value:
            index = int(key.split("_")[-1])
            uv_name = f"Exp_ColorChange_Default_Color_{index}"
            maya.cmds.polyUVSet(shape, create=True, uvSet=uv_name)
            maya.cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_name)
            maya.cmds.delete(shape, constructionHistory=True)
            maya.cmds.polyAutoProjection(
                shape, p=6, o=1, si=1, sc=1, l=2, ps=0.0, cm=0, pb=0, ws=0, ch=0
            )

    # Handle Color_Change facesets
    all_sets = maya.cmds.ls(type="objectSet") or []
    color_change_sets = sorted([s for s in all_sets if "_Render_Color_Change" in s])

    for key, value in channels.items():
        if "_Color_Change" in value:
            index = int(key.split("_")[-1])
            uv_name = f"Exp_ColorChange_{index}"
            faceset_name = value
            if not maya.cmds.objExists(faceset_name):
                continue
            faces = maya.cmds.sets(faceset_name, query=True) or []
            if not faces:
                continue
            preview_faces = [f"{shape}.f[{f.split('.f[')[1]}" for f in faces if ".f[" in f]
            if not preview_faces:
                continue
            maya.cmds.polyUVSet(shape, preview_faces, create=True, uvSet=uv_name)
            maya.cmds.polyUVSet(shape, currentUVSet=True, uvSet=uv_name)
            maya.cmds.polyAutoProjection(
                preview_faces, p=6, o=1, si=0, sc=2, l=2, ps=0.0, cm=0, pb=0, ws=0, ch=0
            )


def export_render_style(ma_path, export_path):
    """Replicate the ved_tool Render export-style procedure.

    1. Open file
    2. Create ColorChange UV sets on Shell (from LEGO_CHANNEL attrs)
    3. Unparent Shell + Detail to world
    4. Combine via polyUnite into a single mesh named m{design_id}
    5. Reparent CommonParts (Knobs/Tubes/Pins) as flat children
    6. Delete construction history
    7. Remove Render objectSets (not part of native export)
    8. Export selected as .ma
    """
    import maya.cmds

    maya.cmds.file(ma_path, open=True, force=True, executeScriptNodes=False)

    roots = [t for t in maya.cmds.ls(assemblies=True) if t.startswith("VME_")]
    if not roots:
        raise RuntimeError("No VME root node found")

    root = roots[0]
    preview_name = get_preview_name(root)
    render_path = f"|{root}|Styles|Render"

    if not maya.cmds.objExists(render_path):
        raise RuntimeError(f"Render node not found: {render_path}")

    # Get Shell mesh(es)
    shell_root = get_node_in_root(render_path, "Shell")
    shell_meshes = get_all_geom_nodes(shell_root) if shell_root else []

    # Create ColorChange UV sets on shell BEFORE combining
    if shell_meshes:
        shell_shape = maya.cmds.listRelatives(shell_meshes[0], shapes=True, fullPath=True)
        if shell_shape:
            setup_color_change_uvsets(shell_shape[0], root)

    # Get Detail meshes
    detail_root = get_node_in_root(render_path, "Detail")
    detail_meshes = get_all_geom_nodes(detail_root) if detail_root else []

    # Get CommonParts
    common_parts_root = get_node_in_root(render_path, "CommonParts")
    knobs = []
    tubes = []
    pins = []
    if common_parts_root:
        cp_meshes = get_all_geom_nodes(common_parts_root)
        for mesh in cp_meshes:
            name_lower = mesh.split("|")[-1].lower()
            if "knob" in name_lower or "logo" in name_lower:
                knobs.append(mesh)
            elif "tube" in name_lower:
                tubes.append(mesh)
            elif "pin" in name_lower:
                pins.append(mesh)

    # Unparent Shell + Detail meshes to world
    meshes_to_combine = []
    all_to_reparent = shell_meshes + detail_meshes
    if all_to_reparent:
        reparented = maya.cmds.parent(all_to_reparent, world=True)
        meshes_to_combine = reparented if isinstance(reparented, list) else [reparented]

    # Combine into single mesh
    if len(meshes_to_combine) > 1:
        combined = maya.cmds.polyUnite(
            meshes_to_combine,
            constructionHistory=False,
            name=preview_name,
        )
        result_node = combined[0] if isinstance(combined, list) else combined
    elif len(meshes_to_combine) == 1:
        result_node = maya.cmds.rename(meshes_to_combine[0], preview_name)
    else:
        raise RuntimeError("No Shell/Detail meshes found to combine")

    # Reparent CommonParts under the result node
    for cnt, kn in enumerate(knobs, start=1):
        label = "Knob" if "knob" in kn.split("|")[-1].lower() else "Logo"
        kn_reparented = maya.cmds.parent(kn, result_node, absolute=True)
        maya.cmds.rename(kn_reparented, f"{label}_{cnt}")

    for cnt, tb in enumerate(tubes, start=1):
        tb_reparented = maya.cmds.parent(tb, result_node, absolute=True)
        maya.cmds.rename(tb_reparented, f"Tube_{cnt}")

    for cnt, pi in enumerate(pins, start=1):
        pi_reparented = maya.cmds.parent(pi, result_node, absolute=True)
        maya.cmds.rename(pi_reparented, f"Pin_{cnt}")

    # Delete construction history on all shapes
    shape_nodes = maya.cmds.ls(result_node, dag=True, long=True, type="mesh") or []
    for shape in shape_nodes:
        if maya.cmds.objExists(shape):
            maya.cmds.delete(shape, constructionHistory=True)

    # Remove Render objectSets (not included in native ved_tool exports)
    all_sets = maya.cmds.ls(type="objectSet") or []
    for s in all_sets:
        if "_Render_" in s:
            try:
                maya.cmds.lockNode(s, lock=False)
                maya.cmds.delete(s)
            except Exception:
                pass

    # Select and export
    maya.cmds.select(result_node, hierarchy=True)
    maya.cmds.file(
        export_path,
        force=True,
        options="v=0;",
        type="mayaAscii",
        preserveReferences=True,
        exportSelected=True,
        executeScriptNodes=False,
    )

    exported_size = os.path.getsize(export_path) if os.path.exists(export_path) else 0
    return {
        "preview_name": preview_name,
        "shell_meshes": len(shell_meshes),
        "detail_meshes": len(detail_meshes),
        "knobs": len(knobs),
        "tubes": len(tubes),
        "pins": len(pins),
        "export_size_kb": round(exported_size / 1024),
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Diagnose Render Manifestation files and run export-style (Render)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--input-dir",
        default=os.path.join(REPO_ROOT, "data", "output", "render-manifestation"),
        help="Directory with Render Manifestation .ma files",
    )
    parser.add_argument(
        "--export-dir",
        default=os.path.join(REPO_ROOT, "data", "output", "export-style"),
        help="Output directory for export-style .ma files (m{id}.ma naming)",
    )
    parser.add_argument(
        "--vme-ids",
        nargs="+",
        metavar="ID",
        help="Only process files matching these VME IDs",
    )
    parser.add_argument(
        "--skip-export",
        action="store_true",
        help="Skip the export-style test (diagnostic only)",
    )
    parser.add_argument(
        "--report",
        default=os.path.join(REPO_ROOT, "data", "output", "render-manifestation", "diagnostic-report.json"),
        help="Path for the JSON diagnostic report",
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

    ma_files = sorted(glob.glob(os.path.join(args.input_dir, "*.ma")))
    if args.vme_ids:
        ids = set(args.vme_ids)
        ma_files = [f for f in ma_files if any(vid in os.path.basename(f) for vid in ids)]

    if not ma_files:
        logger.warning("No .ma files to process.")
        return

    if not args.skip_export:
        os.makedirs(args.export_dir, exist_ok=True)

    logger.info(f"Diagnosing {len(ma_files)} file(s) from {args.input_dir}")
    if not args.skip_export:
        logger.info(f"Export-style output: {args.export_dir}")

    initialize_maya()

    diagnostics = []
    pass_count = 0
    fail_count = 0
    export_success = 0
    export_fail = 0

    try:
        for idx, ma_path in enumerate(ma_files, start=1):
            filename = os.path.basename(ma_path)
            prefix = f"[{idx}/{len(ma_files)}] {filename}"

            # --- Diagnostic ---
            logger.info(f"{prefix} - diagnosing...")
            diag = diagnose_file(ma_path)

            if diag["status"] == "PASS":
                pass_count += 1
                logger.info(f"{prefix} - PASS ({diag.get('mesh_count_render', 0)} render meshes, {len(diag.get('render_sets', []))} sets)")
            else:
                fail_count += 1
                logger.warning(f"{prefix} - FAIL: {diag['issues']}")

            # --- Export Style Test ---
            if not args.skip_export and "CRITICAL" not in str(diag.get("issues", [])):
                export_name = diag.get("preview_name", filename.replace(".ma", ""))
                export_path = os.path.join(args.export_dir, f"{export_name}.ma")
                try:
                    export_result = export_render_style(ma_path, export_path)
                    diag["export"] = export_result
                    export_success += 1
                    logger.info(
                        f"{prefix} - exported as {export_name}.ma "
                        f"(shell={export_result['shell_meshes']}, "
                        f"detail={export_result['detail_meshes']}, "
                        f"knobs={export_result['knobs']}, "
                        f"{export_result['export_size_kb']} KB)"
                    )
                except Exception as e:
                    diag["export_error"] = str(e)
                    export_fail += 1
                    logger.error(f"{prefix} - export FAILED: {e}")

            diagnostics.append(diag)

    finally:
        uninitialize_maya()

    # --- Summary ---
    logger.info("=" * 60)
    logger.info(f"Diagnostic: {pass_count} PASS, {fail_count} FAIL out of {len(ma_files)}")
    if not args.skip_export:
        logger.info(f"Export-style: {export_success} success, {export_fail} failed")

    # --- Write report ---
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2)
    logger.info(f"Report saved: {args.report}")

    # --- Print summary table ---
    print()
    print(f"{'File':<22} {'Status':<6} {'Export Name':<16} {'Shell':<6} {'Detail':<7} {'Knobs':<6} {'KB':<8} {'Issues'}")
    print("-" * 95)
    for d in diagnostics:
        exp = d.get("export", {})
        export_name = exp.get("preview_name", d.get("export_error", "-"))
        if len(str(export_name)) > 15:
            export_name = str(export_name)[:12] + "..."
        issues_str = "; ".join(d.get("issues", [])) if d.get("issues") else "-"
        if len(issues_str) > 20:
            issues_str = issues_str[:17] + "..."
        print(
            f"{d['file']:<22} {d.get('status','?'):<6} "
            f"{str(export_name):<16} "
            f"{exp.get('shell_meshes', '-'):<6} "
            f"{exp.get('detail_meshes', '-'):<7} "
            f"{exp.get('knobs', '-'):<6} "
            f"{exp.get('export_size_kb', '-'):<8} "
            f"{issues_str}"
        )


if __name__ == "__main__":
    main()
