"""Find the final Render ShellShape topology in a .ma file.

Parses Maya ASCII to locate the visible (non-intermediate) ShellShape under
the Render style, then traces construction history if the shape has no
inline vertex data.

Usage:
    python tools/find_render_mesh.py <path_to_ma_file>
"""

import re
import sys


def parse_ma_nodes(filepath):
    """Parse a .ma file into a list of node blocks with their attributes."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    nodes = []
    current = None

    for i, line in enumerate(lines):
        if line.startswith("createNode "):
            if current:
                nodes.append(current)
            m = re.match(
                r'createNode (\S+) -n "([^"]+)"(?:\s+-p "([^"]+)")?', line
            )
            if m:
                current = {
                    "type": m.group(1),
                    "name": m.group(2),
                    "parent": m.group(3),
                    "start_line": i + 1,
                    "attrs": {},
                    "raw_lines": [line],
                }
            else:
                current = None
        elif current:
            current["raw_lines"].append(line)

    if current:
        nodes.append(current)

    return nodes, lines


def extract_mesh_counts(node):
    """Extract vertex, edge, face counts from a mesh node block."""
    vt = 0
    ed = 0
    faces = 0
    uv_sets = {}
    is_intermediate = False
    has_iog = False

    for line in node["raw_lines"]:
        t = line.strip()

        m = re.match(r'setAttr -s (\d+) "\.vt"', t)
        if m:
            vt = int(m.group(1))

        m = re.match(r'setAttr -s (\d+) "\.ed"', t)
        if m:
            ed = int(m.group(1))

        m = re.match(r'setAttr -s (\d+) "\.uvst\[(\d+)\]\.uvsp"', t)
        if m:
            uv_sets[int(m.group(2))] = int(m.group(1))

        if t.startswith("f "):
            faces += 1

        if '".io" yes' in t:
            is_intermediate = True

        if ".iog[" in t or ".iog." in t:
            has_iog = True

    return {
        "vertices": vt,
        "edges": ed,
        "faces": faces,
        "uv_sets": uv_sets,
        "is_intermediate": is_intermediate,
        "has_iog": has_iog,
    }


def parse_connections(lines):
    """Parse all connectAttr lines into a list of (src, dst) tuples."""
    connections = []
    for line in lines:
        if line.startswith("connectAttr "):
            m = re.match(r'connectAttr "([^"]+)" "([^"]+)"', line)
            if m:
                connections.append((m.group(1), m.group(2)))
    return connections


def trace_history_source(target_attr, connections, mesh_nodes_by_name):
    """Trace backwards from target_attr through connections to find the
    source mesh shape that holds actual vertex data.

    Returns (source_node_name, counts_dict) or None.
    """
    visited = set()
    queue = [target_attr]

    while queue:
        attr = queue.pop(0)
        if attr in visited:
            continue
        visited.add(attr)

        node_name = attr.split(".")[0]

        if node_name in mesh_nodes_by_name:
            counts = extract_mesh_counts(mesh_nodes_by_name[node_name])
            if counts["vertices"] > 0:
                return node_name, counts

        for src, dst in connections:
            dst_node = dst.split(".")[0]
            if dst_node == node_name:
                queue.append(src)

    return None


def find_render_shell(filepath):
    """Find the final Render ShellShape topology in a .ma file."""
    nodes, lines = parse_ma_nodes(filepath)
    connections = parse_connections(lines)

    mesh_nodes = [n for n in nodes if n["type"] == "mesh"]
    mesh_nodes_by_name = {}
    for n in mesh_nodes:
        key = n["name"]
        if n["parent"]:
            key = f"{n['parent']}|{n['name']}"
        mesh_nodes_by_name[key] = n
        mesh_nodes_by_name[n["name"]] = n

    render_shells = []
    fallback_shells = []
    for n in mesh_nodes:
        parent = n.get("parent", "") or ""
        if "Render" in parent and "Shell" in parent:
            render_shells.append(n)
        elif "Shell" in n["name"] or "Shell" in parent:
            fallback_shells.append(n)

    if not render_shells:
        if fallback_shells:
            print("No Render|Shell hierarchy found — using Shell shapes directly:\n")
            render_shells = fallback_shells
        else:
            print("No mesh shapes found under Render|Shell or any Shell hierarchy.")
            return None

    print(f"Found {len(render_shells)} mesh shape(s) under Render|Shell:\n")

    visible_shape = None
    for n in render_shells:
        counts = extract_mesh_counts(n)
        role = "INTERMEDIATE" if counts["is_intermediate"] else "VISIBLE"
        print(
            f"  {n['name']} ({role})"
            f"  parent: {n['parent']}"
            f"  line: {n['start_line']}"
        )
        print(
            f"    verts={counts['vertices']}  edges={counts['edges']}  faces={counts['faces']}"
            f"  uv_sets={len(counts['uv_sets'])}  shading={counts['has_iog']}"
        )
        if not counts["is_intermediate"]:
            visible_shape = n

    if not visible_shape:
        print("\nNo non-intermediate shape found under Render.")
        return None

    vis_counts = extract_mesh_counts(visible_shape)
    print(f"\n{'='*60}")
    print(f"Visible shape: {visible_shape['name']}")
    print(f"{'='*60}")

    if vis_counts["vertices"] > 0:
        print(f"\nGeometry stored directly on {visible_shape['name']}:")
        print(f"  Vertices: {vis_counts['vertices']}")
        print(f"  Edges:    {vis_counts['edges']}")
        print(f"  Faces:    {vis_counts['faces']}")
        print(f"  UV sets:  {len(vis_counts['uv_sets'])}")
        return vis_counts

    print(f"\n{visible_shape['name']} has no inline vertex data — tracing construction history...")

    full_path = visible_shape["name"]
    if visible_shape["parent"]:
        full_path = f"{visible_shape['parent']}|{visible_shape['name']}"

    inmesh_attr = f"{full_path}.i"

    for src, dst in connections:
        if dst == inmesh_attr:
            print(f"  inMesh connection: {src} -> {dst}")

            result = trace_history_source(src, connections, mesh_nodes_by_name)
            if result:
                source_name, source_counts = result
                source_node = mesh_nodes_by_name.get(source_name)
                print(f"\n  Source mesh: {source_name} (line {source_node['start_line']})")

                chain = []
                current_attr = src
                chain_visited = set()
                while current_attr:
                    node_name = current_attr.split(".")[0]
                    if node_name in chain_visited:
                        break
                    chain_visited.add(node_name)
                    chain.append(node_name)
                    found_next = False
                    for s, d in connections:
                        if d.split(".")[0] == node_name and s.split(".")[0] not in chain_visited:
                            current_attr = s
                            found_next = True
                            break
                    if not found_next:
                        break

                if chain:
                    print(f"\n  History chain:")
                    print(f"    {' -> '.join(reversed(chain))} -> {visible_shape['name']}")

                print(f"\n  Final topology (from {source_name}):")
                print(f"    Vertices: {source_counts['vertices']}")
                print(f"    Edges:    {source_counts['edges']}")
                print(f"    Faces:    {source_counts['faces']}")
                print(f"    UV sets:  {len(source_counts['uv_sets'])}")
                return source_counts

    print("  Could not trace construction history.")
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <path_to_ma_file>")
        sys.exit(1)

    find_render_shell(sys.argv[1])
