"""Small, versioned contract. Import these names from `rackticker` in plugins."""
from dataclasses import dataclass, field
from typing import Callable

from app.modules.base import Module, RenderContext
from app.providers.base import Provider
from app.outputs.base import FrameSink
from app.core.models import Snapshot


@dataclass(frozen=True)
class PluginContext:
    """Factories do no I/O. Providers read current settings during async fetch()."""
    name: str
    get_settings: Callable[[], dict]
    emit_event: Callable[[str, float], bool]

    @property
    def settings(self):
        return self.get_settings()


@dataclass(frozen=True)
class Plugin:
    name: str
    label: str
    api_version: int = 1
    module: Callable[[], Module] | None = None
    provider: Callable[[PluginContext], Provider] | None = None
    # A provider can feed an existing screen, e.g. local ADS-B -> "flight".
    provider_for: str | None = None
    output: Callable[[PluginContext], FrameSink] | None = None
    defaults: dict = field(default_factory=dict)
    validate_settings: Callable[[dict], None] | None = None
    # Upgrade saved settings from older plugin versions (rename/retire keys)
    # before strict validation, so a deploy never bricks an existing config.
    migrate_settings: Callable[[dict], dict] | None = None
    # Optional UI hints: {"mode": ("auto", "night")} renders a dropdown, and
    # {"mode": "What it does"} shows a one-line help under the field.
    choices: dict = field(default_factory=dict)
    help: dict = field(default_factory=dict)
    # Optional richer controls in Settings, per setting:
    #   {"type": "multi", "options": [...]}   pick several; stored comma separated
    #   {"type": "teams", "leagues": "leagues"} team picker; stored as abbreviations
    #   {"type": "tags"}                        a comma-separated list edited as chips
    #   {"type": "location"}  on "latitude"     city/ZIP search filling latitude and longitude
    #   {"type": "slider", "min": 1, "max": 60, "unit": "s"}
    #   {"type": "hidden"}                      kept out of the page (paths, advanced tuning)
    #   and "label": "Nice name" on any of them.
    ui: dict = field(default_factory=dict)
