"""Allow ``python -m clawflow`` to run the CLI.

Mirrors the ``pyproject.toml`` ``[project.scripts]`` entry point.
"""
from __future__ import annotations

import sys

from clawflow.cli import main


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())