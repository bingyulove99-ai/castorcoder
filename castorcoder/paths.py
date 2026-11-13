"""One place for CastorCoder's user-level state directory.

Tests and portable installs set ``CASTORCODER_HOME`` to keep sessions, the
usage ledger and the history file out of the real home directory.
"""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    override = os.getenv("CASTORCODER_HOME")
    return Path(override).expanduser() if override else Path.home() / ".castorcoder"
