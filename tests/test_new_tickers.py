import importlib.util
import random
import unittest.mock
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


FINANCE = load("finance_test", "plugins/finance/rackticker_finance.py")
WEATHER = load("weather_test", "plugins/weather/rackticker_weather.py")
NEWS = load("news_test", "plugins/news/rackticker_news.py")
TOWN = load("town_test", "plugins/pixel-town/rackticker_town.py")


class NewTickerTests(unittest.TestCase):
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
        # The panel is a window on a wider town: read the truck where it is on screen.
        left = TOWN.TRUCK_X + 6 - town.camera.view
        self.assertTrue(0 <= left < 125, "the camera is not looking at the truck")
        body = {pixels[left + dx, TOWN.STREET_Y - 4] for dx in range(3)}
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


class PixelTownDistrictsTests(unittest.TestCase):
    """The beach and the station: a wider world than the panel, with real data
    behind it when the plugins that fetch it are installed."""

    def test_the_tide_puts_the_water_higher_up_the_sand_at_a_high_tide(self):
        now = datetime(2026, 9, 20, 12, 0)
        high = TOWN.tide_level({"high": True, "time": "2026-09-20 12:00", "then": "2026-09-20 18:12"}, now)
        low = TOWN.tide_level({"high": False, "time": "2026-09-20 12:00", "then": "2026-09-20 18:12"}, now)
        # Lower row number is further up the beach, because the sea is the band above.
        self.assertLess(high, low)
        self.assertAlmostEqual(TOWN.tide_level(None, now), (high + low) / 2, places=5)

    def test_a_wave_moves_the_wash_line_instead_of_standing_still(self):
        town = TOWN.Town()
        town.sea.prime(1.6)
        seen = set()
        for _ in range(90):
            town._sea_step(1 / 30, {"height": 4.0, "period": 9.0})
            seen.add(round(town._wash(25.4)[40], 1))
        self.assertGreater(len(seen), 8, "the water never moved")

    def test_people_stand_on_the_sand_on_the_beach_and_the_pavement_in_town(self):
        self.assertEqual(TOWN.ground_row(20), TOWN.SAND_ROW)
        self.assertEqual(TOWN.ground_row(TOWN.TRUCK_X), TOWN.STREET_Y)
        self.assertEqual(TOWN.ground_row(TOWN.PLATFORM_X + 40), TOWN.STREET_Y)
        # and the ramp between them climbs rather than stepping up through the air
        ramp = [TOWN.ground_row(x) for x in range(TOWN.BEACH_END - 16, TOWN.BEACH_END)]
        self.assertEqual(ramp, sorted(ramp, reverse=True))
        self.assertLessEqual(max(abs(a - b) for a, b in zip(ramp, ramp[1:])), 1)

    def test_a_train_arrives_stops_and_leaves_through_the_tunnel(self):
        town = TOWN.Town()
        town.train, town.train_wait = None, 0.0
        states, rng = [], town.rng
        for _ in range(4000):
            town._trains(1 / 30, 13.0, rng)
            states.append(town.train["state"] if town.train else "away")
            if states.count("leaving") and states[-1] == "away" and "stopped" in states:
                break
        self.assertEqual(["arriving", "stopped", "leaving", "away"],
                         [state for n, state in enumerate(states) if n == 0 or state != states[n - 1]])
        self.assertGreater(states.count("stopped"), 30, "it did not wait long enough to board")

    def test_the_station_board_prefers_a_real_departure_to_an_invented_one(self):
        town = TOWN.Town()
        now = datetime(2026, 9, 20, 9, 5)

        class Snap:
            stale = False
            data = {"lax": {"rows": [{"destination": "San Diego", "time": datetime(2026, 9, 20, 9, 18)}]}}

        class Ctx:
            snapshots = {"departures": Snap()}

        self.assertEqual(("SAN DIEGO", "09:18"), town._departure(Ctx(), now))
        # and without the plugin it still has somewhere to send you
        where, when = town._departure(type("C", (), {"snapshots": {}})(), now)
        self.assertIn(where, TOWN.DESTINATIONS)
        self.assertRegex(when, r"^\d{2}:\d{2}$")

    def test_nothing_drives_onto_the_sand_or_down_the_railway(self):
        town = TOWN.Town()
        for _ in range(3000):
            town._simulate(1 / 30, 13.0, "sun", None)
            for car in town.cars:
                # Only ever between the two portals the road goes under the town through.
                self.assertGreater(car["x"], TOWN.ROAD_L - 2)
                self.assertLess(car["x"], TOWN.ROAD_R + 3)

    def test_the_camera_can_reach_every_district(self):
        town = TOWN.Town()
        reach = [town.camera.look_at(spot) or town.camera.target for spot in town._interests(13.0)]
        self.assertLess(min(reach), 30, "the beach is out of the camera's reach")
        self.assertGreater(max(reach), TOWN.WORLD - TOWN.VIEW - 30, "the station is out of reach")


class PixelTownPolishTests(unittest.TestCase):
    def frame(self, hour, view, weather="sun", t=100.0):
        registry = PluginRegistry(); registry.register(TOWN.plugin)
        config = validate_config({}, registry)
        real = random.Random
        with unittest.mock.patch.object(TOWN.random, "Random", lambda: real(7)):
            town = TOWN.Town()      # the same crowd every time, so only the weather differs
        town.camera.x = town.camera.target = float(view)
        town.camera.dwell = 1e9
        snapshots = {"weather": Snapshot({"icon": weather})}
        now = datetime(2026, 9, 20, hour, 30, tzinfo=timezone.utc)
        for step in range(30):
            frame = town.render(RenderContext(now, t + step / 30, config, snapshots, Message(), SystemStatus(), 1))
        return frame

    def test_two_signs_in_view_never_say_the_same_thing(self):
        seen = []
        original = TOWN.draw_tiny
        TOWN.draw_tiny = lambda frame, text, x, y, colour, *a, **k: (seen.append((y, text)),
                                                                     original(frame, text, x, y, colour, *a, **k))[1]
        try:
            self.frame(14, 128)
        finally:
            TOWN.draw_tiny = original
        signs = [text for y, text in seen if y in (TOWN.SIGNS[0][2] - 8, TOWN.SIGNS[1][2] - 8)]
        self.assertGreaterEqual(len(signs), 2)
        self.assertNotEqual(*signs[-2:])   # the last frame's pair

    def test_the_station_has_a_building_at_the_far_end(self):
        edge = self.frame(14, TOWN.WORLD - TOWN.VIEW).getpixel((TOWN.VIEW - 4, 14))
        self.assertGreater(edge[0], edge[2], "no brick building at the end of the platform")

    def test_the_moon_lays_a_road_of_light_on_a_clear_night_sea(self):
        clear, overcast = self.frame(1, 0), self.frame(1, 0, "cloud")
        water = lambda frame: sum(sum(frame.getpixel((x, y))) for x in range(20, 100) for y in range(19, 24))
        self.assertGreater(water(clear), water(overcast))


class PixelTownLifeTests(unittest.TestCase):
    def town(self, seed=5):
        real = random.Random
        with unittest.mock.patch.object(TOWN.random, "Random", lambda: real(seed)):
            return TOWN.Town()

    def test_only_a_traveller_goes_on_through_the_tunnel_wall(self):
        town = self.town()
        for _ in range(30 * 240):
            town._simulate(1 / 30, 13.0, "sun", None)
            for person in town.people:
                if TOWN.PEOPLE_EAST < person["x"] < TOWN.PLATFORM_X and person["dir"] > 0:
                    self.assertTrue(person["traveller"], f"walked into the tunnel wall at {person['x']:.0f}")

    def test_the_towns_own_trains_keep_the_towns_own_timetable(self):
        town = self.town()
        town.scheduled, town.train, town.served = True, None, None
        hour, left_at, arrived = 12.0, [], []
        for step in range(30 * 3600):
            hour += 1 / 30 / 3600
            town._simulate(1 / 30, hour, "sun", None)
            train = town.train
            if train and train["state"] == "stopped" and train["due"] not in arrived:
                arrived.append(train["due"])
                self.assertLessEqual(hour * 60, train["due"], "the train was late to a departure that had passed")
            if train and train["state"] == "leaving" and train["due"] not in left_at:
                left_at.append(train["due"])
                # It leaves on the dot, never early and never much after.
                self.assertGreaterEqual(hour * 60, train["due"] - 1 / 60)
                self.assertLess(hour * 60, train["due"] + 15 / 60)
        self.assertGreaterEqual(len(left_at), 3, "the timetable ran no trains")
        self.assertEqual(TOWN.Town._next_due(23.2), 23 * 60 + 30)      # thinner late at night
        self.assertEqual(TOWN.Town._next_due(12.1), 12 * 60 + 15)

    def test_the_board_shows_the_train_that_is_standing_there(self):
        class Ctx:
            snapshots = {}
        town = self.town()
        now = datetime(2026, 9, 22, 12, 14, 50)
        where, when = town._departure(Ctx(), now)
        self.assertTrue(town.scheduled)
        self.assertEqual(when, "12:15")
        town.train = {"state": "stopped", "to": where, "x": 300.0, "due": 12 * 60 + 15}
        town.board = (where, when)
        # The board of a train that has come in says NOW, and where that one is going.
        self.assertEqual(town.train["to"], where)

    def test_someone_who_goes_into_a_shop_comes_out_with_a_bag(self):
        town = self.town()
        town.people, town.rng.random = [], lambda: 0.0
        shop = TOWN.SHOPS[3]
        town._spawn_person(float(shop[0] + shop[1] - 6))
        person = town.people[0]
        person.update(dir=1, hungry=False, traveller=False, speed=8.0, dog=False)
        went_in = False
        for _ in range(30 * 20):
            town._simulate(1 / 30, 12.0, "sun", None)
            went_in = went_in or person["inside"] > 0
            if went_in and person["bag"] > 0:
                break
        self.assertTrue(went_in, "nobody went into an open shop")
        self.assertGreater(person["bag"], 0)

    def test_it_rains_umbrellas(self):
        town, frame = self.town(), TOWN.new_frame() if hasattr(TOWN, "new_frame") else None
        from PIL import Image
        frame = Image.new("RGB", (40, 32))
        person = {"x": 10.0, "dir": 1, "speed": 8.0, "shirt": (230, 60, 60), "skin": (255, 214, 170), "pause": 0.0,
                  "slot": None, "carry": 0.0, "bag": 0.0, "dog": False}
        TOWN.Town._person(frame, frame.load(), person, 0.0, TOWN.STREET_Y, True)
        self.assertIn(frame.getpixel((11, TOWN.STREET_Y - 7)), TOWN.UMBRELLAS)
        dry = Image.new("RGB", (40, 32))
        TOWN.Town._person(dry, dry.load(), person, 0.0, TOWN.STREET_Y, False)
        self.assertEqual(dry.getpixel((11, TOWN.STREET_Y - 7)), (0, 0, 0))

    def test_a_patrol_car_flashes_red_and_blue(self):
        from PIL import Image
        seen = set()
        for step in range(12):
            frame = Image.new("RGB", (60, 40))
            car = {"x": 10.0, "dir": 1, "lane": 0, "color": (238, 238, 244), "van": False, "police": True}
            TOWN.Town._car(frame, frame.load(), car, False, step / 6)
            seen.add(frame.getpixel((13, TOWN.STREET_Y)))
        self.assertEqual(seen, {(255, 40, 40), (40, 90, 255)})
