"""Normalized provider models; modules never see vendor-specific API payloads."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, Optional, TypeVar


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


T = TypeVar("T")


@dataclass(frozen=True)
class Snapshot(Generic[T]):
    data: Optional[T]
    updated_at: datetime = field(default_factory=utcnow)
    stale: bool = False
    source: str = "mock"
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Flight:
    callsign: str
    aircraft: str
    origin: str
    destination: str
    altitude_ft: Optional[int]
    speed_kts: Optional[int]
    distance_miles: float
    bearing_deg: int
    vertical_rate: Optional[int]
    registration: str = ""
    eta_minutes: Optional[int] = None


@dataclass(frozen=True)
class Team:
    abbreviation: str
    score: int
    color: str
    logo_url: str = ""
    logo_png: bytes = b""


@dataclass(frozen=True)
class Game:
    home: Team
    away: Team
    status: str
    detail: str
    league: str = ""
    secondary: str = ""
    # Optional display strings, e.g. sportsbook lines ("home_spread": "-4.5").
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Race:
    series: str
    track: str
    start_time: datetime
    car_class: str
    session_type: str
    live: bool = False


@dataclass(frozen=True)
class SystemStatus:
    internet: bool = True
    home_assistant: bool = True
    rack_temp_c: float = 25.6
    notice: str = ""          # something the owner should know, in a few words


@dataclass(frozen=True)
class Message:
    title: str = "RACKTICKER"
    body: str = "ALL SYSTEMS READY"
    scrolling: bool = True


@dataclass(frozen=True)
class PriorityEvent:
    module: str
    duration: float = 10
    priority: int = 10
    reason: str = "event"
