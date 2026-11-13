"""Public statistics API."""

from .aggregate import mean
from .report import summarize

__all__ = ["mean", "summarize"]
