# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Run one of the action's helper modules under an isolated interpreter.

The action starts Python with ``-I``, which keeps the caller's
environment variables, working directory and user site off
``sys.path``, so a ``yaml.py`` or ``lib/`` in the caller's workspace can
never stand in for the real module. ``-I`` also leaves out the action's
own directory, so ``python -I -m lib.rtd_audit`` would find nothing.
This script puts that one directory back and runs the named helper's
``main()`` as ``python -m`` would:

    python -I lib/launch.py lib.rtd_audit --path-prefix …
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

#: The action's root directory, which holds the ``lib`` package.
ACTION_ROOT = Path(__file__).resolve().parent.parent

#: The package every helper the launcher may run lives in.
PACKAGE = "lib"


def main(argv: list[str]) -> int:
    """Run the helper module ``argv[0]`` with the arguments after it."""
    if not argv or not argv[0].startswith(f"{PACKAGE}."):
        raise SystemExit(f"usage: launch.py {PACKAGE}.MODULE [ARG ...]")
    sys.path.insert(0, str(ACTION_ROOT))
    module = importlib.import_module(argv[0])
    sys.argv = [str(module.__file__), *argv[1:]]
    helper = cast("Callable[[], int]", module.main)
    return helper()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
