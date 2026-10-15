"""Reference stub for a community / custom source.

Copy this file to ``clawflow/sources/community/<your_id>.py``, rename the
class, edit ``fetch``, and ``clawflow sources list`` will pick it up.

This module is intentionally dependency-free — no real network calls
happen unless a user copies and re-implements it.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, ClassVar

from clawflow.leads import Lead
from clawflow.sources import Source, register


@register
class ExampleCommunitySource(Source):
    """A no-op source used to demonstrate the registration pattern."""

    key: ClassVar[str] = "community_example"
    label: ClassVar[str] = "Community Example (stub)"
    description: ClassVar[str] = "Reference stub — copy and edit to build your own source."
    default_enabled: ClassVar[bool] = False

    async def validate_config(self, cfg: Any) -> tuple[bool, str]:
        return True, "community_example: no config required"

    def fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        return self._fetch(state, cfg)

    async def _fetch(self, state: dict[str, Any], cfg: Any) -> AsyncIterator[Lead]:
        # No live data — the real implementation would yield Lead(...) records here.
        if False:
            yield Lead(
                id="example:0",
                source=self.key,
                title="example",
                url="https://example.com",
                posted_at=None,
                score=0,
                tags=(),
                raw={},
            )


if __name__ == "__main__":  # pragma: no cover
    print("community_example is a stub — copy and edit.")