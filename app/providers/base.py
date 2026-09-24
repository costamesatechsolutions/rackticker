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


def retry_seconds(failures, every=5.0, longest=60.0):
    """How long a provider that keeps failing is left before it is asked again.

    Providers are asked every five seconds. One that is failing (a feed that is down,
    a server answering "too many requests") is asked half as often each time it fails
    again, up to once a minute, so it is not hammering a struggling server or holding
    up the Pi, and it is back within a minute of the server coming back."""
    return min(longest, every * 2 ** max(0, failures - 1))
