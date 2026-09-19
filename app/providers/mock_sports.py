import re
from app.core.models import Game, Snapshot, Team
from app.providers.base import SportsProvider, finite_number, label


def normalize_game(raw):
    def team(data):
        color = data.get("color", "#e8f0eb")
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#e8f0eb"
        return Team(label(data.get("abbreviation"), 3), int(finite_number(data.get("score", 0), 0, 999)), color)
    status = raw.get("status", "pregame")
    if status not in ("pregame", "live", "final", "goal"):
        raise ValueError("Unknown game status")
    return Game(team(raw["home"]), team(raw["away"]), status, label(raw.get("detail"), 20), label(raw.get("league"), 12))


class MockSportsProvider(SportsProvider):
    scenarios = ("pregame", "live", "final", "goal")

    def __init__(self):
        self.scenario = "final"

    def set_scenario(self, scenario):
        if scenario not in self.scenarios:
            raise ValueError("Unknown sports scenario")
        self.scenario = scenario

    async def fetch(self):
        raw = {"home": {"abbreviation": "LAK", "score": 3, "color": "#d8dfe5"},
               "away": {"abbreviation": "ANA", "score": 1, "color": "#ff972f"},
               "status": self.scenario, "detail": "FINAL", "league": "HOCKEY"}
        if self.scenario == "pregame":
            raw["home"]["score"] = raw["away"]["score"] = 0
            raw["detail"] = "TONIGHT 7:30P"
        elif self.scenario == "live":
            raw.update(home={"abbreviation": "SEA", "score": 24, "color": "#69d981"},
                       away={"abbreviation": "SF", "score": 17, "color": "#ff6856"},
                       detail="Q3 04:52", league="FOOTBALL")
        elif self.scenario == "goal":
            raw["home"]["score"] = 4
            raw["detail"] = "GOAL! LAK"
        return Snapshot(normalize_game(raw))
