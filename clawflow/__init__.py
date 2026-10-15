"""clawflow — freelance-opportunity aggregator for solo web3 and WP devs.

One binary, four upstream signal categories (six sources), dedup, and
three alerters. See ``README.md`` for the elevator pitch and
``docs/sources.md`` for the source protocol.

Importing this package does NOT perform any network I/O — every module
is cheap to import and side-effect-free until you actually run it.
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]


if __name__ == "__main__":  # pragma: no cover
    print(f"clawflow {__version__}")