from dataclasses import dataclass


@dataclass(frozen=True)
class PlaylistEntry:
    id: str
    module: str
    duration: float
    enabled: bool = True
    mode: str = "normal"


def from_config(config):
    return [PlaylistEntry(**entry) for entry in config["playlist"]]
