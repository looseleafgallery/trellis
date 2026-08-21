"""trellis - compute project state from a graph of work, gates, and contracts."""

import sys

# Checked before anything imports PyYAML. The pip bundled with macOS system
# Python 3.9 is old enough to ignore `requires-python`, so the install
# half-succeeds and the first error names a missing third-party module instead
# of the version. Naming the real cause here saves that hunt.
#
# UP036 is off because ruff assumes this file only ever runs on >=3.11, which
# is the very assumption the guard exists to catch.
if sys.version_info < (3, 11):  # noqa: UP036  # pragma: no cover
    raise RuntimeError(
        "trellis requires Python 3.11 or newer; this is "
        f"{sys.version_info.major}.{sys.version_info.minor}. "
        "Create a venv with a newer interpreter:\n"
        "  python3.13 -m venv .venv\n"
        "  .venv/bin/pip install git+https://github.com/looseleafgallery/trellis.git"
    )

from .cache import Cache
from .engine import CycleError, Derived, Engine
from .loader import find_graph_dir, load_graph
from .model import Graph, ModelError, Node, node_from_dict

__version__ = "0.1.0"

__all__ = [
    "Cache",
    "CycleError",
    "Derived",
    "Engine",
    "Graph",
    "ModelError",
    "Node",
    "find_graph_dir",
    "load_graph",
    "node_from_dict",
]
