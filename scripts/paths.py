"""Corporate dependency path resolution for Team Center libraries."""

import os
import sys


def resolve_dep_paths(dep_repo_name="dep-dwf-maya-python", *, relative_to=None):
    """Resolve paths to the corporate dependency repository.

    Returns (python_externals_path, scripts_path) tuple.
    Raises FileNotFoundError if the dep repo cannot be found.
    """
    if relative_to is None:
        relative_to = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    project_root = os.path.abspath(os.path.join(relative_to, "..", dep_repo_name))
    python_externals = os.path.join(project_root, "python_externals")
    scripts_dir = os.path.join(project_root, "Scripts")

    if not os.path.isdir(project_root):
        raise FileNotFoundError(
            f"Dependency repo '{dep_repo_name}' not found at: {project_root}\n"
            f"Expected sibling directory layout:\n"
            f"  {os.path.dirname(project_root)}/\n"
            f"    ├── {dep_repo_name}/\n"
            f"    └── {os.path.basename(relative_to)}/"
        )

    return python_externals, scripts_dir


def ensure_dep_paths_on_sys_path(dep_repo_name="dep-dwf-maya-python", *, relative_to=None):
    """Add corporate dependency paths to sys.path if not already present."""
    python_externals, scripts_dir = resolve_dep_paths(
        dep_repo_name, relative_to=relative_to
    )

    if python_externals not in sys.path:
        sys.path.insert(0, python_externals)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    return python_externals, scripts_dir
