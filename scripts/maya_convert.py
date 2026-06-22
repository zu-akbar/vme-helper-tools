"""Maya standalone initialization and .mb to .ma conversion."""

import logging
import os

logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
DEP_REPO = os.path.abspath(os.path.join(REPO_ROOT, "..", "dep-dwf-maya-python"))
CONNECTIVITY_PLUGIN_PATH = os.path.join(
    DEP_REPO, "python_externals", "connectivity_tool", "2023", "lib"
)


def initialize_maya():
    """Initialize Maya in standalone (headless) mode and load required plugins."""
    import maya.standalone
    maya.standalone.initialize(name="vme_batch")

    import maya.cmds
    if os.path.isdir(CONNECTIVITY_PLUGIN_PATH):
        plugin_file = os.path.join(CONNECTIVITY_PLUGIN_PATH, "ConnectivityTool.mll")
        if os.path.isfile(plugin_file):
            try:
                maya.cmds.loadPlugin(plugin_file)
                logger.info("ConnectivityTool plugin loaded.")
            except Exception as e:
                logger.warning(f"Could not load ConnectivityTool plugin: {e}")

    logger.info("Maya standalone initialized.")


def uninitialize_maya():
    """Shut down Maya standalone."""
    try:
        import maya.standalone
        maya.standalone.uninitialize()
    except Exception:
        pass


def convert_mb_to_ma(mb_path, ma_path):
    """Open a .mb file in Maya and save it as .ma (Maya ASCII).

    Removes unknown nodes that block the save, but preserves unknownDag nodes
    under the Connectivity hierarchy (those are plugin shape nodes for the
    connectivity visualization — they carry valid data and Maya can serialize
    them in .ma format even without the source plugin loaded).
    """
    import maya.cmds

    maya.cmds.file(mb_path, open=True, force=True, executeScriptNodes=False)

    unknown_nodes = maya.cmds.ls(type="unknown") or []
    unknown_dag = maya.cmds.ls(type="unknownDag") or []

    # Identify which unknownDag nodes are connectivity shapes (preserve them)
    connectivity_shapes = set()
    for node in unknown_dag:
        if not maya.cmds.objExists(node):
            continue
        full_path = maya.cmds.ls(node, long=True)
        if full_path and "|Connectivity|" in full_path[0]:
            connectivity_shapes.add(node)

    to_delete = [n for n in unknown_nodes + unknown_dag if n not in connectivity_shapes]

    if to_delete:
        logger.info(f"  Removing {len(to_delete)} unknown node(s) (keeping {len(connectivity_shapes)} connectivity shape(s))...")
        for node in to_delete:
            if maya.cmds.objExists(node):
                try:
                    maya.cmds.lockNode(node, lock=False)
                    maya.cmds.delete(node)
                except Exception:
                    pass

    maya.cmds.file(rename=ma_path)
    maya.cmds.file(save=True, type="mayaAscii", executeScriptNodes=False)
