import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest

from app.core.config import validate_config
from app.core.models import Snapshot, Message, SystemStatus
from app.core.plugins import PluginRegistry
from app.modules.base import RenderContext
from app.core.renderer import validate_frame


ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


F1 = load("f1_test", "plugins/f1-schedule/rackticker_f1.py")
MARKETS = load("markets_test", "plugins/prediction-markets/rackticker_markets.py")
WALL = load("wall_test", "plugins/ticker-wall/rackticker_ticker_wall.py")
FINANCE = load("finance_test", "plugins/finance/rackticker_finance.py")
WEATHER = load("weather_test", "plugins/weather/rackticker_weather.py")
NEWS = load("news_test", "plugins/news/rackticker_news.py")
ARCADE = load("arcade_test", "plugins/arcade/rackticker_arcade.py")
TOWN = load("town_test", "plugins/pixel-town/rackticker_town.py")


class NewTickerTests(unittest.TestCase):
    def test_f1_normalizes_and_renders_next_race(self):
        payload = {"MRData": {"RaceTable": {"Races": [{
            "round": "17", "raceName": "Azerbaijan Grand Prix",
            "date": "2026-09-26", "time": "11:00:00Z",
            "Circuit": {"circuitName": "Baku City Circuit"},
        }]}}}
        row = F1.normalize(payload, "America/Los_Angeles")
        self.assertEqual(row["race"], "AZERBAIJAN GP")
        self.assertIn("BAKU", row["circuit"])
        registry = PluginRegistry()
        registry.register(F1.plugin)
        config = validate_config({"plugins": {"f1": {}}, "modules": {"f1": {"enabled": True}},
                                  "playlist": [{"id": "f1", "module": "f1"}]}, registry)
        context = RenderContext(datetime.now(timezone.utc), 5, config,
                                {"f1": Snapshot(row)}, Message("X", "X"), SystemStatus())
        self.assertIsNotNone(validate_frame(F1.F1Module().render(context)).getbbox())

    def test_market_parsers_accept_public_api_formats(self):
        poly = MARKETS.polymarket_events([{"title": "Will it rain?", "volume24hr": "1200", "markets": [{
            "question": "Will it rain?", "outcomes": '["No", "Yes"]',
            "outcomePrices": '["0.38", "0.62"]', "oneDayPriceChange": 0.05}]}])
        kalshi = MARKETS.kalshi_events({"events": [{"title": "Will the Padres win?", "markets": [
            {"yes_sub_title": "Padres", "yes_bid": 41, "yes_ask": 45, "volume_24h": 800}]}]})
        self.assertTrue(poly[0]["binary"])
        self.assertEqual(round(poly[0]["outcomes"][0]["probability"]), 62)
        self.assertEqual(round(poly[0]["outcomes"][0]["change"]), 5)
        self.assertEqual(round(kalshi[0]["outcomes"][0]["probability"]), 43)
        self.assertEqual(kalshi[0]["source"], "KALSHI")

    def test_ticker_wall_modes_render_without_full_frame_fill(self):
        registry = PluginRegistry()
        registry.register(WALL.plugin)
        config = validate_config({"plugins": {"ticker_wall": {}},
                                  "modules": {"ticker_wall": {"enabled": True}},
                                  "playlist": [{"id": "wall", "module": "ticker_wall"}]}, registry)
        for elapsed in (1, 9, 17):
            wall = WALL.TickerWall()
            # A phrase's first instant can be dark while its letters fly in; the
            # sign must be lit a moment later and never flood the whole panel.
            frames = [validate_frame(wall.render(RenderContext(datetime.now(timezone.utc), moment, config, {},
                                                               Message("X", "X"), SystemStatus())))
                      for moment in (elapsed, elapsed + .4)]
            self.assertTrue(any(frame.getbbox() for frame in frames))
            for frame in frames:
                self.assertLess(sum(pixel != (0, 0, 0) for pixel in frame.get_flattened_data()), 3000)

    def test_new_plugin_settings_are_strict(self):
        with self.assertRaises(ValueError):
            F1.validate({"timezone": "bad/zone", "refresh_seconds": 900})
        with self.assertRaises(ValueError):
            MARKETS.validate({"refresh_seconds": 1, "cycle_seconds": 8})
        with self.assertRaises(ValueError):
            WALL.validate({"mode": "blink", "scene_seconds": 8,
                           "arena_items": "A", "times_square_items": "B", "taqueria_items": "C"})

    def test_finance_names_companies_and_picks_their_own_headline(self):
        self.assertEqual(FINANCE.company_name("Meta Platforms, Inc.", "META"), "META PLATFORMS")
        self.assertEqual(FINANCE.company_name("NVIDIA Corporation", "NVDA"), "NVIDIA")
        self.assertEqual(FINANCE.company_name("MARA Holdings, Inc.", "MARA"), "")
        now = 1_000_000
        payload = {"news": [
            {"title": "Stock Market Today: Dow wavers", "publisher": "IBD", "providerPublishTime": now - 60},
            {"title": "Apple’s iPhone goes on sale", "publisher": "Reuters.com", "providerPublishTime": now - 7200},
            {"title": "Apple old news", "publisher": "X", "providerPublishTime": now - 90 * 3600}]}
        item = FINANCE.headline_for(payload, "AAPL", "APPLE", now)
        self.assertEqual((item["title"], item["publisher"]), ("Apple’s iPhone goes on sale", "Reuters"))
        self.assertIsNone(FINANCE.headline_for({"news": payload["news"][:1]}, "AAPL", "APPLE", now))

    def test_finance_normalizes_and_renders_sparkline(self):
        payload = {"chart": {"result": [{"meta": {
            "symbol": "BTC-USD", "regularMarketPrice": 61234.5,
            "chartPreviousClose": 60000, "currency": "USD"},
            "indicators": {"quote": [{"close": [60000, None, 60500, 61234.5]}]}}]}}
        row = FINANCE.normalize(payload, "BTC-USD")
        self.assertGreater(row["change"], 2)
        registry = PluginRegistry(); registry.register(FINANCE.plugin)
        config = validate_config({"plugins": {"finance": {}},
                                  "modules": {"finance": {"enabled": True}},
                                  "playlist": [{"id": "finance", "module": "finance"}]}, registry)
        context = RenderContext(datetime.now(timezone.utc), 0, config,
                                {"finance": Snapshot({"indices": [dict(row, label="BTC")], "tape": [row]})},
                                Message(), SystemStatus())
        self.assertIsNotNone(validate_frame(FINANCE.FinanceModule().render(context)).getbbox())

    def test_the_stock_tape_sits_out_the_weekend(self):
        """Friday's closing prices are not news on a Sunday."""
        from zoneinfo import ZoneInfo
        registry = PluginRegistry(); registry.register(FINANCE.plugin)
        module = FINANCE.FinanceModule()
        row = {"symbol": "NVDA", "price": 1, "change": 1, "label": "NVDA", "closes": [1, 2]}
        snapshots = {"finance": Snapshot({"indices": [row], "tape": [row]})}
        ny = ZoneInfo("America/New_York")

        def shown(when, setting):
            config = validate_config({"plugins": {"finance": {"when": setting}},
                                      "modules": {"finance": {"enabled": True}},
                                      "playlist": [{"id": "finance", "module": "finance"}]}, registry)
            return module.available(RenderContext(when, 0, config, snapshots, Message(), SystemStatus()))

        saturday = datetime(2026, 9, 19, 12, 0, tzinfo=ny)
        wednesday_open = datetime(2026, 9, 16, 12, 0, tzinfo=ny)
        wednesday_night = datetime(2026, 9, 16, 23, 0, tzinfo=ny)
        self.assertFalse(shown(saturday, "weekdays"))
        self.assertTrue(shown(wednesday_open, "weekdays"))
        self.assertTrue(shown(wednesday_night, "weekdays"))   # a weekday evening still counts
        self.assertFalse(shown(wednesday_night, "open"))      # unless you asked for trading hours
        self.assertTrue(shown(wednesday_open, "open"))
        self.assertTrue(shown(saturday, "always"))            # or for it never to go away

    def test_weather_normalizes_and_renders_graphic(self):
        payload = {"current": {"temperature_2m": 72.4, "apparent_temperature": 71,
                               "weather_code": 61, "wind_speed_10m": 8.2},
                   "daily": {"sunrise": ["2026-09-15T06:36"],
                             "sunset": ["2026-09-15T18:58"]}}
        row = WEATHER.normalize(payload)
        self.assertEqual((row["label"], row["icon"]), ("RAIN", "rain"))
        registry = PluginRegistry(); registry.register(WEATHER.plugin)
        config = validate_config({"plugins": {"weather": {}},
                                  "modules": {"weather": {"enabled": True}},
                                  "playlist": [{"id": "weather", "module": "weather"}]}, registry)
        context = RenderContext(datetime.now(timezone.utc), 0, config,
                                {"weather": Snapshot(row)}, Message(), SystemStatus())
        frame = validate_frame(WEATHER.WeatherModule().render(context))
        self.assertIsNotNone(frame.getbbox())
        self.assertGreater(sum(pixel == (73, 170, 255)
                               for pixel in frame.get_flattened_data()), 3)

    def test_news_parses_rss_and_atom(self):
        self.assertEqual(NEWS.headlines("<rss><channel><item><title>One story</title></item>"
                                        "<item><title>Two story</title></item></channel></rss>"),
                         ["One story", "Two story"])
        atom = '<feed xmlns="urn:x"><entry><title>Atom story</title></entry></feed>'
        self.assertEqual(NEWS.headlines(atom), ["Atom story"])

    def test_all_arcade_games_simulate_canonical_frames(self):
        registry = PluginRegistry(); registry.register(ARCADE.plugin)
        for mode in ARCADE.GAMES:
            config = validate_config({"plugins": {"arcade": {"mode": mode}},
                                      "modules": {"arcade": {"enabled": True}},
                                      "playlist": [{"id": "arcade", "module": "arcade"}]}, registry)
            module, frames = ARCADE.Arcade(), set()
            for step in range(8 * 30):
                context = RenderContext(datetime.now(timezone.utc), step / 30, config, {},
                                        Message(), SystemStatus())
                frame = validate_frame(module.render(context))
                if step >= 5 * 30:
                    frames.add(frame.tobytes())
            self.assertGreater(len(frames), 5, mode)

    def test_pixel_town_lives_through_a_day_in_every_weather(self):
        from datetime import timedelta
        from app.core.models import Flight
        registry = PluginRegistry(); registry.register(TOWN.plugin)
        config = validate_config({"plugins": {"town": {}}, "modules": {"town": {"enabled": True}},
                                  "playlist": [{"id": "town", "module": "town"}]}, registry)
        flight = Snapshot(Flight("UAL1234", "B39M", "---", "---", 9000, 300, 4.0, 90, 0), source="adsb_network")
        module = TOWN.Town()
        start = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
        for hour, icon in enumerate(("sun", "partly", "cloud", "fog", "rain", "storm", "snow") * 4):
            snapshots = {"weather": Snapshot({"icon": icon, "temperature": 70}), "flight": flight}
            for step in range(20):
                context = RenderContext(start + timedelta(hours=hour % 24), hour * 10 + step / 30, config,
                                        snapshots, Message(), SystemStatus(), hour)
                validate_frame(module.render(context))

    def test_nobody_walks_through_the_taco_truck(self):
        """People on the pavement pass behind the truck; drawn over it they walked
        through its side, which is what it looked like."""
        town = TOWN.Town()
        town.people = []
        town._spawn_person(float(TOWN.TRUCK_X + 6))
        person = town.people[0]
        person.update(dir=1, hungry=False, fed=True, speed=8.0, dog=False, slot=None, carry=0.0)
        frame = self.town_frame(town, hour=12)
        pixels = frame.load()
        body = {pixels[TOWN.TRUCK_X + 6 + dx, TOWN.STREET_Y - 4] for dx in range(3)}
        self.assertNotIn(person["shirt"], body, "the person is drawn over the truck")

    def test_the_queue_forms_one_behind_another(self):
        """Everyone used to stop on the same pixel, so customers stood inside each other."""
        town = TOWN.Town()
        town.people = []
        for x in (TOWN.WINDOW_X - 24, TOWN.WINDOW_X - 32, TOWN.WINDOW_X - 40):
            town._spawn_person(float(x))
            town.people[-1].update(dir=1, hungry=True, fed=False, speed=9.0, dog=False, slot=None, carry=0.0)
        for _ in range(30 * 8):
            town._simulate(1 / 30, 12.5, "sun", None)
        queued = [p for p in town.people if p["slot"] is not None]
        self.assertGreaterEqual(len(queued), 2, "nobody queued at an open truck")
        slots = [p["slot"] for p in queued]
        self.assertEqual(len(set(slots)), len(slots), "two customers were given the same place")
        # Everyone who has reached their place is a clear step from the next person.
        # (Someone still walking up to the back of the queue may be anywhere.)
        settled = sorted(p["x"] for p in queued if abs(p["x"] - town._slot_x(p["slot"])) < .5)
        for near, far in zip(settled, settled[1:]):
            self.assertGreaterEqual(far - near, TOWN.QUEUE_GAP - .01, "two customers in one spot")

    def test_a_customer_is_served_and_walks_off_with_the_taco(self):
        town = TOWN.Town()
        town.people = []
        town._spawn_person(float(TOWN.WINDOW_X - 6))
        person = town.people[0]
        person.update(dir=1, hungry=True, fed=False, speed=9.0, dog=False, slot=None, carry=0.0)
        for _ in range(30 * 12):
            town._simulate(1 / 30, 12.5, "sun", None)
            if person["fed"]:
                break
        self.assertTrue(person["fed"], "nobody was ever served")
        self.assertGreater(person["carry"], 0, "served without being handed anything")
        self.assertIsNone(person["slot"], "still standing at the window after being served")

    def test_the_queue_breaks_up_when_the_truck_closes(self):
        town = TOWN.Town()
        town.people = []
        town._spawn_person(float(TOWN.WINDOW_X))
        person = town.people[0]
        person.update(dir=1, hungry=True, fed=False, speed=9.0, dog=False, slot=0, carry=0.0)
        town._simulate(1 / 30, 15.0, "sun", None)      # between lunch and dinner: shutter down
        self.assertIsNone(person["slot"], "left queueing at a closed truck for ever")

    def town_frame(self, town, hour):
        from datetime import datetime, timezone
        registry = PluginRegistry(); registry.register(TOWN.plugin)
        config = validate_config({"plugins": {"town": {}}, "modules": {"town": {"enabled": True}},
                                  "playlist": [{"id": "town", "module": "town"}]}, registry)
        now = datetime(2026, 9, 15, hour, 30, tzinfo=timezone.utc)
        return town.render(RenderContext(now, 1.0, config, {}, Message(), SystemStatus(), 1))

    def test_pixel_quest_hero_never_gets_stuck(self):
        """A platform ending one column before a step left no headroom to jump it."""
        import random
        for seed in range(40):
            game = ARCADE.Platformer(random.Random(seed))
            best, still = game.x, 0.0
            for _ in range(30 * 90):
                world = game.world
                game.update(1 / 30)
                if game.dead or game.cleared or game.world != world or abs(game.x - best) > 1:
                    best, still = game.x, 0.0
                else:
                    still += 1 / 30
                self.assertLess(still, 4, f"seed {seed}: hero stuck at x={game.x:.1f}")

    def test_retired_arcade_scene_settings_migrate(self):
        registry = PluginRegistry(); registry.register(ARCADE.plugin)
        config = validate_config({"plugins": {"arcade": {"mode": "runner", "scene_seconds": 12}}}, registry)
        self.assertEqual(config["plugins"]["arcade"]["mode"], "auto")


if __name__ == "__main__":
    unittest.main()


class NewsFreshnessTests(unittest.TestCase):
    def test_old_stories_are_skipped_and_newest_lead(self):
        from datetime import timedelta
        news = load("news_fresh_test", "plugins/news/rackticker_news.py")
        now = datetime.now(timezone.utc)
        rows = [{"title": "old", "published": now - timedelta(hours=32)},
                {"title": "new", "published": now - timedelta(hours=1)},
                {"title": "newer", "published": now - timedelta(minutes=5)},
                {"title": "undated", "published": None}]
        self.assertEqual([row["title"] for row in news.fresh(rows, 12, now)], ["newer", "new", "undated"])
