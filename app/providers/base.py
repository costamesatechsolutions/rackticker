"""Providers do I/O outside rendering; adapt external payloads at this boundary."""
from abc import ABC, abstractmethod
import math
from typing import Generic, TypeVar

from app.core.models import Snapshot, Flight, Game

T = TypeVar("T")


class Provider(ABC, Generic[T]):
    @abstractmethod
    async def fetch(self) -> Snapshot[T]:
        """Return normalized data. Raise on failure; the runtime retains stale data."""

    async def close(self):
        """Release network clients and other resources on shutdown."""


class FlightProvider(Provider[Flight]):
    pass


class SportsProvider(Provider[Game]):
    pass



def finite_number(value, low, high):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a numeric data field")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"Number must be finite and within {low}–{high}")
    return result


def label(value, limit=32):
    return str(value or "---").strip().upper()[:limit] or "---"
