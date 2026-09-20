from datetime import datetime, timezone
from importlib import util
from pathlib import Path
import unittest

from app.core.config import validate_config
from app.core.models import Game, Snapshot, Team
from app.modules.base import RenderContext
from app.modules.sports import SportsModule


def load_plugin_module():
    path = Path(__file__).resolve().parents[1] / "plugins/free-sports/rackticker_free_sports.py"
    spec = util.spec_from_file_location("free_sports_test", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_sportsbook():
    path = Path(__file__).resolve().parents[1] / "plugins/sportsbook/rackticker_sportsbook.py"
    spec = util.spec_from_file_location("sportsbook_test", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LiveOddsTests(unittest.TestCase):
    """A score means more next to the number the game is being measured against."""

    def setUp(self):
        self.book = load_sportsbook()
        self.game = Game(league="NBA", status="live", detail="Q3 4:12",
                         home=Team(abbreviation="LAL", score=88, color="552583", logo_png=None),
                         away=Team(abbreviation="BOS", score=91, color="007A33", logo_png=None))

    def line(self, extra):
        return self.book.Sportsbook.live_line(self.game, extra)

    def test_the_favourite_is_the_side_laying_the_points(self):
        self.assertEqual(self.line({"home_spread": "-4.5", "away_spread": "+4.5", "total": "224.5"}),
                         "LAL -4.5  O/U 224.5")
        self.assertEqual(self.line({"home_spread": "+3", "away_spread": "-3"}), "BOS -3")

    def test_a_total_on_its_own_is_still_worth_showing(self):
        self.assertEqual(self.line({"total": "48"}), "O/U 48")

    def test_a_game_with_no_market_says_nothing(self):
        self.assertEqual(self.line({}), "")
        self.assertEqual(self.line({"home_spread": "PK", "away_spread": "PK"}), "")

    def test_it_never_outgrows_the_corner_it_sits_in(self):
        long = self.line({"home_spread": "-10.5", "away_spread": "+10.5", "total": "1234.5"})
        self.assertLessEqual(len(long), 20)


class FreeSportsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sports = load_plugin_module()

    def game(self, game_id, start, state="FUT", home="ANA", away="LAK"):
        return {
            "id": game_id,
            "startTimeUTC": start,
            "gameState": state,
            "homeTeam": {"abbrev": home, "score": 3},
            "awayTeam": {"abbrev": away, "score": 2},
        }

    def test_rotation_uses_whole_nearest_slate_without_future_favorite_bias(self):
        now = datetime(2026, 9, 14, 20, tzinfo=timezone.utc)
        future = self.sports.normalize_nhl_game(self.game(1, "2026-09-20T20:00:00Z"), "UTC")
        nearby = self.sports.normalize_nhl_game(self.game(2, "2026-09-15T20:00:00Z", home="BOS"), "UTC")
        same_slate = self.sports.normalize_nhl_game(
            self.game(4, "2026-09-15T23:00:00Z", home="DET", away="TOR"), "UTC")
        live = self.sports.normalize_nhl_game(self.game(3, "2026-09-14T19:00:00Z", "LIVE"), "UTC")
        result = self.sports.rotation([future, nearby, same_slate, live], {"ANA"}, now)
        self.assertEqual(result[0]["id"], live["id"])
        self.assertEqual({item["id"] for item in result},
                         {nearby["id"], same_slate["id"], live["id"]})

    def test_normalizes_future_live_and_final(self):
        future = self.sports.normalize_nhl_game(
            self.game(1, "2026-09-20T02:00:00Z"), "America/Los_Angeles")
        self.assertEqual((future["game"].home.abbreviation,
                          future["game"].away.abbreviation), ("ANA", "LAK"))
        self.assertEqual(future["game"].status, "pregame")
        self.assertIn("9/19", future["game"].detail)
        self.assertTrue(future["game"].home.logo_url.endswith("/ana.png"))
        self.assertTrue(future["game"].away.logo_url.endswith("/la.png"))

        raw = self.game(2, "2026-09-14T19:00:00Z", "LIVE")
        raw.update(periodDescriptor={"number": 2, "periodType": "REG"},
                   clock={"timeRemaining": "08:31", "inIntermission": False})
        self.assertEqual(self.sports.normalize_nhl_game(raw, "UTC")["game"].detail, "P2 08:31")
        raw.update(gameState="FINAL", periodDescriptor={"periodType": "OT"})
        self.assertEqual(self.sports.normalize_nhl_game(raw, "UTC")["game"].detail, "FINAL OT")

    def test_normalizes_espn_score(self):
        raw = {"id": "7", "date": "2026-09-15T00:15Z", "competitions": [{
            "competitors": [
                {"homeAway": "home", "score": "31", "team": {"abbreviation": "KC", "color": "e31837"}},
                {"homeAway": "away", "score": "10", "team": {"abbreviation": "DEN", "color": "0a2343"}},
            ],
            "status": {"type": {"state": "in", "shortDetail": "4:41 - 4th"}},
            "odds": [{"details": "KC -3.5", "overUnder": 46.5}],
        }]}
        item = self.sports.normalize_espn_event(raw, "NFL", "America/Los_Angeles")
        self.assertEqual(item["game"].league, "NFL")
        self.assertEqual(item["game"].status, "live")
        self.assertEqual((item["game"].home.score, item["game"].away.score), (31, 10))
        self.assertEqual(item["game"].secondary, "KC -3.5 O/U 46.5")

    def test_settings_validation(self):
        valid = {"leagues": "NHL,NFL,MLB,NBA", "favorite_teams": "ANA,LAK,SD",
                "refresh_seconds": 30, "cycle_seconds": 8,
                 "timezone": "America/Los_Angeles", "show_logos": True}
        self.sports.validate(valid)
        for leagues in ("", "NHL,NHL", "CRICKET"):
            with self.assertRaises(ValueError):
                self.sports.validate({**valid, "leagues": leagues})

    def test_team_color_bars_are_straight(self):
        game = Game(Team("ANA", 2, "#fc4c02"), Team("LAK", 1, "#a2aaad"),
                    "live", "P2 08:31", "NHL")
        config = validate_config({})
        context = RenderContext(datetime.now(timezone.utc), 0, config,
                                {"sports": Snapshot(game, source="nhl")}, None, None)
        frame = SportsModule().render(context)
        # Away (LAK) stripe on the left edge, home (ANA) on the right.
        for x, color in ((0, (162, 170, 173)), (1, (162, 170, 173)),
                         (126, (252, 76, 2)), (127, (252, 76, 2))):
            self.assertTrue(all(frame.getpixel((x, y)) == color for y in range(9, 25)))


if __name__ == "__main__":
    unittest.main()


class LivePlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sports = load_plugin_module()

    def mlb(self, home, away, detail, **extra):
        return Game(Team("SD", home, "#FFC425"), Team("LAD", away, "#005A9C"), "live", detail, "MLB", "", extra)

    def test_situation_reads_like_a_tv_bug(self):
        situation = {"balls": 3, "strikes": 1, "outs": 2, "onFirst": True, "onSecond": True, "onThird": False,
                     "batter": {"athlete": {"shortName": "S. Frelick"}},
                     "lastPlay": {"id": "9", "text": "Pitch 4 : Ball 3"}}
        extra = self.sports.live_situation(situation, {})
        self.assertEqual((extra["bases"], extra["outs"], extra["count"], extra["batter"]), ("110", "2", "3-1", "S. Frelick"))
        football = self.sports.live_situation({"shortDownDistanceText": "3rd & 7", "possessionText": "LV 18",
                                               "possession": "12", "isRedZone": True}, {"12": "home"})
        self.assertEqual((football["down"], football["ball"], football["red_zone"]), ("3RD & 7", "home", "1"))

    def test_double_play_from_two_outs_at_once(self):
        before = self.mlb(1, 0, "TOP 3RD", outs="0", bases="100", play_id="1")
        after = self.mlb(1, 0, "TOP 3RD", outs="2", bases="000", play_id="2", play="Pitch 1 : Strike 1")
        self.assertEqual(self.sports.play_call(after, before), ("DOUBLE PLAY", "home", ""))  # SD fields in the top

    def test_grand_slam_from_the_bases_clearing(self):
        before = self.mlb(0, 0, "BOT 5TH", outs="1", bases="111", play_id="1")
        after = self.mlb(4, 0, "BOT 5TH", outs="1", bases="000", play_id="2")
        self.assertEqual(self.sports.play_call(after, before), ("GRAND SLAM", "home", ""))

    def test_quiet_pitch_is_no_play(self):
        before = self.mlb(0, 0, "BOT 5TH", outs="1", bases="100", play_id="1")
        after = self.mlb(0, 0, "BOT 5TH", outs="1", bases="010", play_id="2", play="Pitch 3 : Ball 2")
        self.assertIsNone(self.sports.play_call(after, before))

    def test_interception_goes_to_the_defense(self):
        before = Game(Team("KC", 7, "#E31837"), Team("LV", 3, "#A5ACAF"), "live", "Q2 5:00", "NFL", "", {"ball": "home", "play_id": "1"})
        after = Game(Team("KC", 7, "#E31837"), Team("LV", 3, "#A5ACAF"), "live", "Q2 4:51", "NFL", "",
                     {"ball": "away", "play_id": "2", "play": "P.Mahomes pass INTERCEPTED by M.Crosby"})
        self.assertEqual(self.sports.play_call(after, before), ("INTERCEPTION", "away", ""))


class HockeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sports = load_plugin_module()

    def test_nhl_live_reads_shots_power_play_and_the_last_goal(self):
        raw = {"homeTeam": {"abbrev": "ANA", "sog": 21}, "awayTeam": {"abbrev": "LAK", "sog": 30},
               "situation": {"homeTeam": {"situationDescriptions": ["PP"]}, "awayTeam": {}, "timeRemaining": "01:14"},
               "goals": [{"teamAbbrev": "ANA", "lastName": {"default": "Terry"}, "goalsToDate": 12, "strength": "pp"}]}
        extra = self.sports.nhl_live(raw)
        self.assertEqual((extra["home_sog"], extra["away_sog"], extra["pp"], extra["pp_time"]), ("21", "30", "home", "01:14"))
        self.assertEqual((extra["last_goal"], extra["last_goal_kind"], extra["goal_count"]), ("TERRY (12)", "POWER-PLAY GOAL", "1"))

    def test_goal_and_power_play_calls(self):
        game = lambda extra, home=1: Game(Team("ANA", home, "#fc4c02"), Team("LAK", 0, "#a2aaad"), "live", "P2 10:00", "NHL", "", extra)
        before = game({"goal_count": "0"}, 0)
        after = game({"goal_count": "1", "last_goal": "TERRY (12)", "last_goal_team": "ANA", "last_goal_kind": "GOAL"})
        self.assertEqual(self.sports.play_call(after, before), ("GOAL", "home", "TERRY (12)"))
        self.assertEqual(self.sports.play_call(game({"goal_count": "1", "pp": "away"}), game({"goal_count": "1"})),
                         ("POWER PLAY", "away", ""))


class FootballTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sports = load_plugin_module()

    def test_big_gain_and_red_zone_are_called(self):
        game = lambda extra: Game(Team("KC", 7, "#E31837"), Team("LV", 3, "#A5ACAF"), "live", "Q2 5:00", "NFL", "", extra)
        before = game({"ball": "home", "play_id": "1"})
        after = game({"ball": "home", "play_id": "2", "play": "P.Mahomes pass deep left to T.Kelce for 34 yards"})
        self.assertEqual(self.sports.play_call(after, before), ("BIG PLAY +34", "home", ""))
        self.assertEqual(self.sports.play_call(game({"ball": "home", "red_zone": "1", "play_id": "2"}), before),
                         ("RED ZONE", "home", ""))
        field = self.sports.live_situation({"shortDownDistanceText": "3rd & 7", "yardLine": 82, "distance": 7,
                                            "homeTimeouts": 2, "possession": "1"}, {"1": "home"})
        self.assertEqual((field["yard"], field["togo"], field["home_timeouts"]), ("82", "7", "2"))
