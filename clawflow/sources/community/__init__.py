"""Drop-in directory for user / community source modules.

Any ``.py`` file in this directory that subclasses ``clawflow.sources.Source``
and uses the ``@register`` decorator will be picked up by the registry
after a restart. See ``clawflow/sources/github_bounties.py`` for a
concrete reference implementation.

This file itself exists only so Python treats the directory as a
package; importing it is harmless.
"""
from __future__ import annotations

__all__: list[str] = []


if __name__ == "__main__":  # pragma: no cover
    print("clawflow.sources.community — drop your @register'd Source subclasses here.")