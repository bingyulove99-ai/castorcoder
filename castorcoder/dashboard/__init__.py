"""CastorCoder Local Cockpit / Dashboard."""

from __future__ import annotations

from castorcoder.dashboard.server import (
    DEFAULT_PORT,
    DashboardHandler,
    start_dashboard,
)

__all__ = [
    "DEFAULT_PORT",
    "DashboardHandler",
    "start_dashboard",
]
