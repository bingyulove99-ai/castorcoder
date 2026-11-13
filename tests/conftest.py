"""Keep pytest's temporary directories inside the repository.

Some Windows environments deny access to the shared ``%TEMP%\\pytest-of-*``
directory.  Using a repository-local ``.tmp/pytest`` root keeps the suite
hermetic and works identically on CI.  An explicit ``--basetemp`` still wins.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Keep the user's real ~/.castorcoder/config.toml, sessions and usage ledger
# out of the test run.  This is also what makes the key/doctor tests hermetic.
os.environ.setdefault(
    "CASTORCODER_HOME", str(Path(__file__).resolve().parent.parent / ".tmp" / "pytest-home")
)


@pytest.fixture(autouse=True)
def _isolate_environment():
    """Undo the process-wide side effect of ``load_dotenv`` between tests."""

    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)


def pytest_configure(config) -> None:
    if getattr(config.option, "basetemp", None):
        return
    fallback = Path(__file__).resolve().parent.parent / ".tmp" / "pytest"
    fallback.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(fallback)
