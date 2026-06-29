"""
Full corpus VME pipeline: Fetch + Extract Render Manifestation + Collect Data.

Processes all VMEs from a CSV input through three phases:
1. Fetch .mb from Team Center and convert to .ma
2. Extract Render Manifestation (normal hierarchy)
3. Collect comprehensive scene data for ADR completion

Run with:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/pipeline_full_corpus.py [OPTIONS]

Examples:
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/pipeline_full_corpus.py --dry-run
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/pipeline_full_corpus.py --phase fetch
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/pipeline_full_corpus.py --phase extract --phase collect
    & "C:\\Program Files\\Autodesk\\Maya2023\\bin\\mayapy.exe" tools/pipeline_full_corpus.py --vme-ids VX0013393 VX0003001 --limit 2
"""

import sys
import os
import argparse
import logging
import json
import csv
import signal
import tempfile
import traceback
from datetime import datetime

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, REPO_ROOT)

from scripts.paths import ensure_dep_paths_on_sys_path
ensure_dep_paths_on_sys_path(relative_to=REPO_ROOT)

from team_center.TeamCenter import RequestFailed

from scripts.logging import setup_logging
from scripts.teamcenter import create_tc_session
from scripts.vme import download_vme_mb
from scripts.maya_convert import initialize_maya, uninitialize_maya, convert_mb_to_ma

logger = logging.getLogger("pipeline")

NON_RENDER_STYLES = ("Realtime", "BIPrint")
NON_RENDER_SET_MARKERS = ("_Realtime_", "_BIPrint_")


# ---------------------------------------------------------------------------
# Progress Manifest
# ---------------------------------------------------------------------------
class ProgressManifest:
    def __init__(self, path):
        self.path = path
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"meta": {}, "items": {}}

    def save(self):
        self.data["meta"]["last_updated"] = datetime.now().isoformat(timespec="seconds")
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        try:
            os.replace(tmp_path, self.path)
        except PermissionError:
            # Windows: target file may be held by another process; fall back to direct write
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def is_phase_done(self, vme_id, phase):
        item = self.data["items"].get(vme_id, {})
        return item.get("phases", {}).get(phase, {}).get("status") == "done"

    def get_phase_output(self, vme_id, phase):
        item = self.data["items"].get(vme_id, {})
        return item.get("phases", {}).get(phase, {}).get("output")

    def mark_done(self, vme_id, phase, output=None):
        self._ensure_item(vme_id)
        entry = {"status": "done", "completed_at": datetime.now().isoformat(timespec="seconds")}
        if output:
            entry["output"] = output
        self.data["items"][vme_id]["phases"][phase] = entry

    def mark_error(self, vme_id, phase, error_msg):
        self._ensure_item(vme_id)
        self.data["items"][vme_id]["phases"][phase] = {
            "status": "error",
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "error": error_msg[:500],
        }

    def mark_skipped(self, vme_id, phase):
        self._ensure_item(vme_id)
        self.data["items"][vme_id]["phases"][phase] = {"status": "skipped"}

    def set_item_meta(self, vme_id, rev, superdesign):
        self._ensure_item(vme_id)
        self.data["items"][vme_id]["rev"] = rev
        self.data["items"][vme_id]["superdesign"] = superdesign

    def _ensure_item(self, vme_id):
        if vme_id not in self.data["items"]:
            self.data["items"][vme_id] = {"phases": {}}

    def summary(self):
        counts = {"fetch": {}, "extract": {}, "collect": {}}
        for item in self.data["items"].values():
            for phase in ("fetch", "extract", "collect"):
                status = item.get("phases", {}).get(phase, {}).get("status", "pending")
                counts[phase][status] = counts[phase].get(status, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# CSV Loading
# ---------------------------------------------------------------------------
def load_csv(csv_path):
    """Read the input CSV and return list of (vme_id, rev, superdesign) tuples."""
    items = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            vme_id = row.get("TCID", "").strip()
            rev = row.get("REV", "").strip()
            superdesign = row.get("SUPERDESIGN", "").strip()
            if vme_id:
                items.append((vme_id, rev, superdesign))
    return items


# ---------------------------------------------------------------------------
# Phase 1: Fetch + Convert
# ---------------------------------------------------------------------------
def phase_fetch(session, vme_id, rev, temp_dir, vme_output_dir):
    """Download .mb from TC and convert to .ma. Returns output .ma path."""
    mb_path = download_vme_mb(session, vme_id, rev, temp_dir)
    ma_filename = f"{vme_id}_{rev}.ma" if rev else f"{vme_id}.ma"
    ma_path = os.path.join(vme_output_dir, ma_filename)
    convert_mb_to_ma(mb_path, ma_path)
    try:
        os.remove(mb_path)
    except OSError:
        pass
    return ma_path


# ---------------------------------------------------------------------------
# Phase 2: Extract Render Manifestation (normal hierarchy)
# ---------------------------------------------------------------------------
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
    for child in children:
        name = child.split("|")[-1]
        if name != "Render":
            maya.cmds.delete(child)

    all_sets = maya.cmds.ls(type="objectSet") or []
    for s in all_sets:
        if any(marker in s for marker in NON_RENDER_SET_MARKERS):
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

    to_delete = [n for n in unknown_nodes + unknown_dag if n not in connectivity_shapes]
    if to_delete:
        for node in to_delete:
            if maya.cmds.objExists(node):
                try:
                    maya.cmds.lockNode(node, lock=False)
                    maya.cmds.delete(node)
                except Exception:
                    pass


def phase_extract(ma_input_path, render_output_dir):
    """Extract Render manifestation from a converted VME .ma file."""
    import maya.cmds

    maya.cmds.file(ma_input_path, open=True, force=True, executeScriptNodes=False)
    extract_render_style()
    remove_unknown_nodes()

    output_path = os.path.join(render_output_dir, os.path.basename(ma_input_path))
    maya.cmds.file(rename=output_path)
    maya.cmds.file(save=True, type="mayaAscii", executeScriptNodes=False)
    return output_path


# ---------------------------------------------------------------------------
# Phase 3: Comprehensive Data Collection
# ---------------------------------------------------------------------------
def collect_node_types():
    """Count all node types in the scene."""
    import maya.cmds

    all_nodes = maya.cmds.ls(showType=True) or []
    type_counts = {}
    for i in range(0, len(all_nodes), 2):
        if i + 1 < len(all_nodes):
            node_type = all_nodes[i + 1]
            type_counts[node_type] = type_counts.get(node_type, 0) + 1
    return type_counts


def collect_custom_attributes():
    """Collect user-defined attributes from all transforms."""
    import maya.cmds

    attrs_found = {}
    all_transforms = maya.cmds.ls(type="transform") or []

    for t in all_transforms:
        user_attrs = maya.cmds.listAttr(t, userDefined=True) or []
        for attr in user_attrs:
            if attr in attrs_found:
                attrs_found[attr]["count"] += 1
                continue
            try:
                attr_type = maya.cmds.getAttr(f"{t}.{attr}", type=True)
                value = maya.cmds.getAttr(f"{t}.{attr}")
                attrs_found[attr] = {
                    "type": attr_type,
                    "sample_value": str(value)[:200] if value is not None else None,
                    "sample_node": t,
                    "count": 1,
                }
            except Exception:
                attrs_found[attr] = {"type": "unknown", "count": 1}

    return attrs_found


def collect_uv_sets():
    """Get all UV set names across all meshes with point counts."""
    import maya.cmds

    all_meshes = maya.cmds.ls(type="mesh") or []
    uv_set_data = {}

    for mesh in all_meshes:
        try:
            uvsets = maya.cmds.polyUVSet(mesh, query=True, allUVSets=True) or []
            for uvset in uvsets:
                if uvset not in uv_set_data:
                    uv_set_data[uvset] = {"mesh_count": 0}
                uv_set_data[uvset]["mesh_count"] += 1
        except Exception:
            pass

    return uv_set_data


def collect_object_sets():
    """Get all objectSet and creaseSet names with member counts."""
    import maya.cmds

    all_sets = maya.cmds.ls(type="objectSet") or []
    crease_sets = maya.cmds.ls(type="creaseSet") or []
    result = {}

    for s in all_sets + crease_sets:
        try:
            members = maya.cmds.sets(s, query=True) or []
            node_type = maya.cmds.nodeType(s)
            result[s] = {"type": node_type, "member_count": len(members)}
        except Exception:
            result[s] = {"type": "objectSet", "member_count": -1}

    return result


def collect_partitions():
    """Get partition nodes and their member sets."""
    import maya.cmds

    partitions = maya.cmds.ls(type="partition") or []
    result = {}
    for p in partitions:
        try:
            members = maya.cmds.partition(p, query=True) or []
            result[p] = members
        except Exception:
            result[p] = []

    return result


def collect_connectivity():
    """Deep introspection of Connectivity hierarchy."""
    import maya.cmds

    result = {
        "present": False,
        "cbox_count": 0,
        "boolean_count": 0,
        "legacy_count": 0,
        "conn_field_count": 0,
        "conn_feature_count": 0,
        "field_type_names": [],
        "field_planar_type_names": [],
        "field_type_codes": [],
        "shape_node_types": [],
        "cylinder_types": [],
        "feature_types": [],
    }

    roots = [t for t in maya.cmds.ls(assemblies=True) if t.startswith("VME_")]
    if not roots:
        return result

    root = roots[0]
    conn_path = f"|{root}|Connectivity"

    if not maya.cmds.objExists(conn_path):
        return result

    result["present"] = True

    cbox_path = f"{conn_path}|Cbox"
    if maya.cmds.objExists(cbox_path):
        cbox_children = maya.cmds.listRelatives(cbox_path, children=True, fullPath=True) or []
        for child in cbox_children:
            name = child.split("|")[-1]
            if name == "Boolean":
                bool_children = maya.cmds.listRelatives(child, children=True, fullPath=True) or []
                result["boolean_count"] = len(bool_children)
                for bcyl in bool_children:
                    features = maya.cmds.listRelatives(bcyl, children=True, fullPath=True, type="transform") or []
                    for feat in features:
                        if maya.cmds.attributeQuery("CylinderType", node=feat, exists=True):
                            try:
                                cyl_type = maya.cmds.getAttr(f"{feat}.CylinderType", asString=True)
                                if cyl_type and cyl_type not in result["cylinder_types"]:
                                    result["cylinder_types"].append(cyl_type)
                            except Exception:
                                pass
            elif name == "Legacy":
                legacy_children = maya.cmds.listRelatives(child, children=True, fullPath=True) or []
                result["legacy_count"] = len(legacy_children)
            else:
                result["cbox_count"] += 1

    conn_path_inner = f"{conn_path}|Conn"
    if maya.cmds.objExists(conn_path_inner):
        conn_fields = maya.cmds.listRelatives(conn_path_inner, children=True, fullPath=True, type="transform") or []
        result["conn_field_count"] = len(conn_fields)

        for field in conn_fields:
            if maya.cmds.attributeQuery("FieldTypeName", node=field, exists=True):
                try:
                    ftn = maya.cmds.getAttr(f"{field}.FieldTypeName")
                    if ftn and ftn not in result["field_type_names"]:
                        result["field_type_names"].append(ftn)
                except Exception:
                    pass

            if maya.cmds.attributeQuery("FieldPlanarTypeName", node=field, exists=True):
                try:
                    ptn = maya.cmds.getAttr(f"{field}.FieldPlanarTypeName")
                    if ptn and ptn not in result["field_planar_type_names"]:
                        result["field_planar_type_names"].append(ptn)
                except Exception:
                    pass

            if maya.cmds.attributeQuery("FieldTypeCode", node=field, exists=True):
                try:
                    ftc = maya.cmds.getAttr(f"{field}.FieldTypeCode")
                    if ftc is not None and ftc not in result["field_type_codes"]:
                        result["field_type_codes"].append(ftc)
                except Exception:
                    pass

            features = maya.cmds.listRelatives(field, children=True, fullPath=True, type="transform") or []
            result["conn_feature_count"] += len(features)

            for feat in features:
                if maya.cmds.attributeQuery("featureType", node=feat, exists=True):
                    try:
                        ft = maya.cmds.getAttr(f"{feat}.featureType", asString=True)
                        if ft and ft not in result["feature_types"]:
                            result["feature_types"].append(ft)
                    except Exception:
                        pass

                shapes = maya.cmds.listRelatives(feat, shapes=True, fullPath=True) or []
                for shape in shapes:
                    try:
                        ntype = maya.cmds.nodeType(shape)
                        if ntype and ntype not in result["shape_node_types"]:
                            result["shape_node_types"].append(ntype)
                    except Exception:
                        pass

    return result


def collect_shading():
    """Collect shading network information."""
    import maya.cmds

    shading_engines = maya.cmds.ls(type="shadingEngine") or []
    materials = maya.cmds.ls(materials=True) or []
    material_info_count = len(maya.cmds.ls(type="materialInfo") or [])

    material_types = {}
    for mat in materials:
        mt = maya.cmds.nodeType(mat)
        material_types[mt] = material_types.get(mt, 0) + 1

    return {
        "shading_engine_count": len(shading_engines),
        "material_count": len(materials),
        "material_info_count": material_info_count,
        "material_types": material_types,
    }


def collect_hierarchy():
    """Record hierarchy structure."""
    import maya.cmds

    roots = [t for t in maya.cmds.ls(assemblies=True) if t.startswith("VME_")]
    if not roots:
        return {}

    root = roots[0]
    root_children = maya.cmds.listRelatives(root, children=True) or []

    render_children = []
    render_path = f"|{root}|Styles|Render"
    if maya.cmds.objExists(render_path):
        render_children = maya.cmds.listRelatives(render_path, children=True) or []

    return {
        "root": root,
        "root_children": sorted(root_children),
        "render_children": sorted(render_children),
    }


def collect_mesh_stats():
    """Collect mesh statistics (vertex/face/edge counts)."""
    import maya.cmds

    all_meshes = maya.cmds.ls(type="mesh", long=True) or []
    stats = {
        "total_meshes": len(all_meshes),
        "render_meshes": 0,
        "connectivity_meshes": 0,
        "total_vertices": 0,
        "total_faces": 0,
        "total_edges": 0,
    }

    for mesh in all_meshes:
        if "|Render|" in mesh or "|Styles|" in mesh:
            stats["render_meshes"] += 1
        elif "|Connectivity|" in mesh:
            stats["connectivity_meshes"] += 1

        try:
            verts = maya.cmds.polyEvaluate(mesh, vertex=True)
            faces = maya.cmds.polyEvaluate(mesh, face=True)
            edges = maya.cmds.polyEvaluate(mesh, edge=True)
            stats["total_vertices"] += verts if isinstance(verts, int) else 0
            stats["total_faces"] += faces if isinstance(faces, int) else 0
            stats["total_edges"] += edges if isinstance(edges, int) else 0
        except Exception:
            pass

    return stats


def collect_rig_info():
    """Check for Rig hierarchy and skeleton/skinCluster nodes."""
    import maya.cmds

    roots = [t for t in maya.cmds.ls(assemblies=True) if t.startswith("VME_")]
    if not roots:
        return {"present": False}

    root = roots[0]
    rig_path = f"|{root}|Rig"

    if not maya.cmds.objExists(rig_path):
        return {"present": False}

    joints = maya.cmds.ls(rig_path, dag=True, type="joint") or []
    skin_clusters = maya.cmds.ls(type="skinCluster") or []

    return {
        "present": True,
        "joint_count": len(joints),
        "skin_cluster_count": len(skin_clusters),
    }


def phase_collect(ma_path, collect_dir):
    """Open extracted .ma and collect comprehensive scene data."""
    import maya.cmds

    maya.cmds.file(ma_path, open=True, force=True, executeScriptNodes=False)

    vme_id = os.path.basename(ma_path).split("_")[0]

    data = {
        "vme_id": vme_id,
        "file": os.path.basename(ma_path),
        "file_size_kb": round(os.path.getsize(ma_path) / 1024),
        "node_types": collect_node_types(),
        "custom_attributes": collect_custom_attributes(),
        "uv_sets": collect_uv_sets(),
        "object_sets": collect_object_sets(),
        "partitions": collect_partitions(),
        "connectivity": collect_connectivity(),
        "shading": collect_shading(),
        "hierarchy": collect_hierarchy(),
        "mesh_stats": collect_mesh_stats(),
        "rig": collect_rig_info(),
    }

    basename = os.path.splitext(os.path.basename(ma_path))[0]
    out_file = os.path.join(collect_dir, f"{basename}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return data


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def aggregate_corpus_report(collect_dir, output_path):
    """Read per-file JSON files from collect_dir and produce aggregated corpus-analysis.json."""
    all_node_types = {}
    all_attributes = {}
    all_uv_sets = {}
    all_set_patterns = {}
    all_field_type_names = set()
    all_field_planar_type_names = set()
    all_field_type_codes = set()
    all_shape_node_types = set()
    all_cylinder_types = set()
    all_feature_types = set()
    all_material_types = {}
    root_children_freq = {}
    render_children_freq = {}
    connectivity_present_count = 0
    rig_present_count = 0
    total_processed = 0
    total_vertices = 0
    total_faces = 0
    total_meshes_list = []
    cbox_counts = []
    boolean_counts = []
    conn_field_counts = []

    import glob as glob_mod
    json_files = sorted(glob_mod.glob(os.path.join(collect_dir, "*.json")))
    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        total_processed += 1

        for ntype, count in data.get("node_types", {}).items():
            all_node_types[ntype] = all_node_types.get(ntype, 0) + count

        for attr_name, attr_info in data.get("custom_attributes", {}).items():
            if attr_name not in all_attributes:
                all_attributes[attr_name] = {
                    "type": attr_info.get("type", "unknown"),
                    "file_count": 0,
                    "sample_values": [],
                }
            all_attributes[attr_name]["file_count"] += 1
            sample = attr_info.get("sample_value")
            if sample and len(all_attributes[attr_name]["sample_values"]) < 5:
                if sample not in all_attributes[attr_name]["sample_values"]:
                    all_attributes[attr_name]["sample_values"].append(sample)

        for uvset_name, uvset_info in data.get("uv_sets", {}).items():
            all_uv_sets[uvset_name] = all_uv_sets.get(uvset_name, 0) + 1

        for set_name in data.get("object_sets", {}).keys():
            parts = set_name.split("_Render_")
            if len(parts) == 2:
                suffix = "_Render_" + parts[1]
                all_set_patterns[suffix] = all_set_patterns.get(suffix, 0) + 1
            else:
                all_set_patterns[set_name] = all_set_patterns.get(set_name, 0) + 1

        conn = data.get("connectivity", {})
        if conn.get("present"):
            connectivity_present_count += 1
            cbox_counts.append(conn.get("cbox_count", 0))
            boolean_counts.append(conn.get("boolean_count", 0))
            conn_field_counts.append(conn.get("conn_field_count", 0))
            all_field_type_names.update(conn.get("field_type_names", []))
            all_field_planar_type_names.update(conn.get("field_planar_type_names", []))
            all_field_type_codes.update(conn.get("field_type_codes", []))
            all_shape_node_types.update(conn.get("shape_node_types", []))
            all_cylinder_types.update(conn.get("cylinder_types", []))
            all_feature_types.update(conn.get("feature_types", []))

        shading = data.get("shading", {})
        for mt, count in shading.get("material_types", {}).items():
            all_material_types[mt] = all_material_types.get(mt, 0) + count

        hierarchy = data.get("hierarchy", {})
        for child in hierarchy.get("root_children", []):
            root_children_freq[child] = root_children_freq.get(child, 0) + 1
        for child in hierarchy.get("render_children", []):
            render_children_freq[child] = render_children_freq.get(child, 0) + 1

        if data.get("rig", {}).get("present"):
            rig_present_count += 1

        mesh_stats = data.get("mesh_stats", {})
        total_vertices += mesh_stats.get("total_vertices", 0)
        total_faces += mesh_stats.get("total_faces", 0)
        total_meshes_list.append(mesh_stats.get("total_meshes", 0))

    def _stats(values):
        if not values:
            return {"min": 0, "max": 0, "mean": 0, "median": 0}
        values_sorted = sorted(values)
        n = len(values_sorted)
        return {
            "min": values_sorted[0],
            "max": values_sorted[-1],
            "mean": round(sum(values_sorted) / n, 2),
            "median": values_sorted[n // 2],
        }

    report = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "total_processed": total_processed,
            "source_dir": collect_dir,
        },
        "node_types": dict(sorted(all_node_types.items(), key=lambda x: -x[1])),
        "custom_attributes": dict(sorted(all_attributes.items(), key=lambda x: -x[1]["file_count"])),
        "uv_sets": dict(sorted(all_uv_sets.items(), key=lambda x: -x[1])),
        "object_set_patterns": dict(sorted(all_set_patterns.items(), key=lambda x: -x[1])),
        "connectivity": {
            "present_in_files": connectivity_present_count,
            "field_type_names": sorted(all_field_type_names),
            "field_planar_type_names": sorted(all_field_planar_type_names),
            "field_type_codes": sorted(all_field_type_codes),
            "shape_node_types": sorted(all_shape_node_types),
            "cylinder_types": sorted(all_cylinder_types),
            "feature_types": sorted(all_feature_types),
            "cbox_count_stats": _stats(cbox_counts),
            "boolean_count_stats": _stats(boolean_counts),
            "conn_field_count_stats": _stats(conn_field_counts),
        },
        "shading": {
            "material_types": all_material_types,
        },
        "hierarchy": {
            "root_children_frequency": dict(sorted(root_children_freq.items(), key=lambda x: -x[1])),
            "render_children_frequency": dict(sorted(render_children_freq.items(), key=lambda x: -x[1])),
        },
        "rig": {
            "present_in_files": rig_present_count,
        },
        "mesh_stats": {
            "total_vertices_corpus": total_vertices,
            "total_faces_corpus": total_faces,
            "meshes_per_file": _stats(total_meshes_list),
        },
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Corpus analysis saved: {output_path}")
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Full corpus VME pipeline: Fetch + Extract + Collect",
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
        "--phase",
        action="append",
        choices=["fetch", "extract", "collect"],
        help="Phase(s) to run (repeatable; default: all three)",
    )
    parser.add_argument(
        "--csv",
        default=os.path.join(REPO_ROOT, "data", "input", "3DFLOW-list-type-active.csv"),
        help="Input CSV file path",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(REPO_ROOT, "data", "output"),
        help="Base output directory (default: data/output)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Progress manifest path (default: {output-dir}/pipeline-progress.json)",
    )
    parser.add_argument(
        "--vme-ids",
        nargs="+",
        metavar="ID",
        help="Process only these VME IDs (for testing)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N items (0 = no limit)",
    )
    parser.add_argument(
        "--save-interval",
        type=int,
        default=10,
        help="Save manifest every N items (default: 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without doing it",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    parser.add_argument("--log-file", metavar="PATH")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    args = parse_args()
    setup_logging(args.log_level, args.log_file)

    phases = args.phase or ["fetch", "extract", "collect"]
    manifest_path = args.manifest or os.path.join(args.output_dir, "pipeline-progress.json")

    vme_output_dir = os.path.join(args.output_dir, "VME")
    render_output_dir = os.path.join(args.output_dir, "Render-Manifestation")
    collect_dir = os.path.join(args.output_dir, "corpus-per-file")
    analysis_path = os.path.join(args.output_dir, "corpus-analysis.json")
    temp_dir = os.path.join(tempfile.gettempdir(), "DWF", "pipeline_corpus")

    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(vme_output_dir, exist_ok=True)
    os.makedirs(render_output_dir, exist_ok=True)
    os.makedirs(collect_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    manifest = ProgressManifest(manifest_path)

    logger.info(f"Loading CSV: {args.csv}")
    work_items = load_csv(args.csv)
    logger.info(f"Total VMEs in CSV: {len(work_items)}")

    if args.vme_ids:
        id_set = set(args.vme_ids)
        work_items = [(vid, rev, sd) for vid, rev, sd in work_items if vid in id_set]
        logger.info(f"Filtered to {len(work_items)} item(s) by --vme-ids")

    if args.limit > 0:
        work_items = work_items[:args.limit]
        logger.info(f"Limited to {args.limit} item(s)")

    if args.dry_run:
        logger.info("--- DRY RUN ---")
        logger.info(f"Phases: {phases}")
        logger.info(f"Items to process: {len(work_items)}")
        for i, (vid, rev, sd) in enumerate(work_items[:20], 1):
            print(f"  {i:>5}. {vid}  rev={rev or '(empty)'}  sd={sd or '(none)'}")
        if len(work_items) > 20:
            print(f"  ... and {len(work_items) - 20} more")
        summary = manifest.summary()
        logger.info(f"Manifest state: {json.dumps(summary, indent=2)}")
        return

    manifest.data["meta"]["csv_source"] = args.csv
    manifest.data["meta"]["total_items"] = len(work_items)
    manifest.data["meta"]["phases"] = phases
    if "started_at" not in manifest.data["meta"]:
        manifest.data["meta"]["started_at"] = datetime.now().isoformat(timespec="seconds")

    shutdown_requested = False

    def handle_sigint(sig, frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            logger.warning("Force quit.")
            sys.exit(1)
        shutdown_requested = True
        logger.warning("Shutdown requested — finishing current item...")

    signal.signal(signal.SIGINT, handle_sigint)

    initialize_maya()

    session = None
    if "fetch" in phases:
        logger.info(f"Connecting to Team Center ({args.env})...")
        session = create_tc_session(args.env)

    success_count = 0
    error_count = 0

    try:
        for idx, (vme_id, rev, superdesign) in enumerate(work_items, 1):
            if shutdown_requested:
                logger.info("Shutting down gracefully.")
                break

            manifest.set_item_meta(vme_id, rev, superdesign)
            prefix = f"[{idx}/{len(work_items)}] {vme_id} rev={rev or '?'}"

            item_failed = False

            # --- Phase: Fetch ---
            if "fetch" in phases:
                if manifest.is_phase_done(vme_id, "fetch"):
                    logger.debug(f"{prefix} fetch: already done, skipping")
                else:
                    if not rev:
                        manifest.mark_error(vme_id, "fetch", "No REV in CSV")
                        item_failed = True
                    else:
                        try:
                            logger.info(f"{prefix} - fetching + converting...")
                            ma_path = phase_fetch(session, vme_id, rev, temp_dir, vme_output_dir)
                            manifest.mark_done(vme_id, "fetch", output=ma_path)
                        except RequestFailed as e:
                            logger.error(f"{prefix} - TC fetch failed: {e}")
                            manifest.mark_error(vme_id, "fetch", str(e))
                            item_failed = True
                        except Exception as e:
                            logger.error(f"{prefix} - fetch failed: {e}")
                            logger.debug(traceback.format_exc())
                            manifest.mark_error(vme_id, "fetch", str(e))
                            item_failed = True

            if item_failed:
                manifest.mark_skipped(vme_id, "extract")
                manifest.mark_skipped(vme_id, "collect")
                error_count += 1
                if idx % args.save_interval == 0:
                    manifest.save()
                continue

            # --- Phase: Extract ---
            if "extract" in phases:
                if manifest.is_phase_done(vme_id, "extract"):
                    logger.debug(f"{prefix} extract: already done, skipping")
                else:
                    fetch_output = manifest.get_phase_output(vme_id, "fetch")
                    if not fetch_output or not os.path.isfile(fetch_output):
                        ma_filename = f"{vme_id}_{rev}.ma" if rev else f"{vme_id}.ma"
                        fetch_output = os.path.join(vme_output_dir, ma_filename)

                    if not os.path.isfile(fetch_output):
                        logger.warning(f"{prefix} - extract: source .ma not found, skipping")
                        manifest.mark_error(vme_id, "extract", "Source .ma not found")
                        manifest.mark_skipped(vme_id, "collect")
                        error_count += 1
                        if idx % args.save_interval == 0:
                            manifest.save()
                        continue

                    try:
                        logger.info(f"{prefix} - extracting Render...")
                        output_path = phase_extract(fetch_output, render_output_dir)
                        manifest.mark_done(vme_id, "extract", output=output_path)
                    except Exception as e:
                        logger.error(f"{prefix} - extract failed: {e}")
                        logger.debug(traceback.format_exc())
                        manifest.mark_error(vme_id, "extract", str(e))
                        manifest.mark_skipped(vme_id, "collect")
                        error_count += 1
                        if idx % args.save_interval == 0:
                            manifest.save()
                        continue

            # --- Phase: Collect ---
            if "collect" in phases:
                if manifest.is_phase_done(vme_id, "collect"):
                    logger.debug(f"{prefix} collect: already done, skipping")
                else:
                    extract_output = manifest.get_phase_output(vme_id, "extract")
                    if not extract_output or not os.path.isfile(extract_output):
                        ma_filename = f"{vme_id}_{rev}.ma" if rev else f"{vme_id}.ma"
                        extract_output = os.path.join(render_output_dir, ma_filename)

                    if not os.path.isfile(extract_output):
                        logger.warning(f"{prefix} - collect: extracted .ma not found, skipping")
                        manifest.mark_error(vme_id, "collect", "Extracted .ma not found")
                        error_count += 1
                        if idx % args.save_interval == 0:
                            manifest.save()
                        continue

                    try:
                        logger.info(f"{prefix} - collecting data...")
                        phase_collect(extract_output, collect_dir)
                        manifest.mark_done(vme_id, "collect")
                    except Exception as e:
                        logger.error(f"{prefix} - collect failed: {e}")
                        logger.debug(traceback.format_exc())
                        manifest.mark_error(vme_id, "collect", str(e))
                        error_count += 1
                        if idx % args.save_interval == 0:
                            manifest.save()
                        continue

            success_count += 1
            if idx % args.save_interval == 0:
                manifest.save()
                logger.info(f"  Progress saved ({idx}/{len(work_items)})")

    finally:
        manifest.save()
        uninitialize_maya()

    # --- Aggregation ---
    if "collect" in phases and os.path.isdir(collect_dir):
        logger.info("Aggregating corpus data...")
        aggregate_corpus_report(collect_dir, analysis_path)

    # --- Summary ---
    logger.info("=" * 60)
    logger.info(f"Pipeline complete: {success_count} success, {error_count} errors")
    logger.info(f"Manifest: {manifest_path}")
    summary = manifest.summary()
    for phase_name, counts in summary.items():
        logger.info(f"  {phase_name}: {counts}")


if __name__ == "__main__":
    main()
