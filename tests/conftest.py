"""Pytest configuration — adds the repo root to ``sys.path`` so the
``clawflow`` package is importable without an editable install.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))