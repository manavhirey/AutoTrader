"""Path shim (Hitchhiker's Guide §Test Suite).

Inserts the repository root onto sys.path so `import orb_bot` resolves whether
or not the package is installed. Test modules use `from .context import orb_bot`.
"""

import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import orb_bot  # noqa: E402  (import after sys.path manipulation, re-exported for tests)

__all__ = ["orb_bot"]
