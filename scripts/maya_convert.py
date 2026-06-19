"""Maya standalone initialization and .mb to .ma conversion."""

import logging

logger = logging.getLogger(__name__)


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
    """Open a .mb file in Maya and save it as .ma (Maya ASCII).

    Removes unknown nodes (from unloaded plugins) that would block the format change.
    """
    import maya.cmds

    maya.cmds.file(mb_path, open=True, force=True, executeScriptNodes=False)

    unknown_nodes = maya.cmds.ls(type="unknown") or []
    unknown_dag = maya.cmds.ls(type="unknownDag") or []
    all_unknown = unknown_nodes + unknown_dag
    if all_unknown:
        logger.info(f"  Removing {len(all_unknown)} unknown node(s) to allow .ma save...")
        for node in all_unknown:
            if maya.cmds.objExists(node):
                try:
                    maya.cmds.lockNode(node, lock=False)
                    maya.cmds.delete(node)
                except Exception:
                    pass

    maya.cmds.file(rename=ma_path)
    maya.cmds.file(save=True, type="mayaAscii", executeScriptNodes=False)
