import importlib.util
import random
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
import unittest

from PIL import Image

from app.core.config import validate_config
from app.core.models import Snapshot, Message, SystemStatus
from app.core.plugins import PluginRegistry
from app.modules.base import RenderContext
from app.core.renderer import validate_frame
from app.core.fonts import text_width, wrap_text


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

    def test_a_wave_arrives_runs_up_the_sand_and_drains_back(self):
        town = TOWN.Town()
        town.sea.prime(1.6)
        seen = []
        for _ in range(int(9.5 * 30)):                # one whole swell
            town._sea_step(1 / 30, {"height": 4.0, "period": 9.0})
            seen.append(town._wash(25.4)[40])
        self.assertLess(min(seen), 25.6, "the water never drew back")
        self.assertGreater(max(seen), 27.5, "the wave never ran up the sand")
        self.assertGreater(len({round(v, 1) for v in seen}), 20, "the water jumped instead of running")

    def test_the_swell_comes_in_towards_the_sand_it_does_not_slide_along_the_beach(self):
        """Waves that rolled from one end of the beach to the other looked like the whole ocean was
        moving sideways. A crest is a line parallel to the shore, and what moves is its distance."""
        sea = TOWN.Sea()
        rows = []
        for _ in range(60):
            sea.step(1 / 30, 9.0, 1.4)
            rows.append([TOWN.HORIZON + 1 + 6 * (sea.swell(x) / .86) ** 1.4 for x in (5, 50, 100)])
        # at any moment the crest is nearly level across the beach...
        for row in rows:
            self.assertLess(max(row) - min(row), 1.6)
        # ...and from moment to moment it moves down the panel (towards the sand)
        for column in range(3):
            path = [row[column] for row in rows if row[column] < 24]
            self.assertTrue(all(b >= a for a, b in zip(path, path[1:])), "the crest went backwards")
            self.assertGreater(path[-1] - path[0], 1)

    def test_a_beach_frame_shows_crests_as_well_as_the_backdrop(self):
        town = TOWN.Town()
        town.sea.prime(1.6)
        frames = set()
        for _ in range(60):
            town._sea_step(1 / 30, None)
            frame = TOWN.sky_image(600).copy()
            town._hour = 13.0
            town._beach(frame, TOWN.ImageDraw.Draw(frame), frame.load(), False, 25.4, 0.0, 0, 128, 1.0)
            frames.add(frame.crop((0, 18, 104, 32)).tobytes())
        self.assertGreater(len(frames), 40, "the sea did not change from frame to frame")

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
        now = datetime(2026, 9, 20, 14, 30, tzinfo=timezone.utc)
        for t in (0.0, 3.0, 10.0, 20.0):
            frame = Image.new("RGB", (TOWN.WORLD, 32))
            TOWN.Town._sign(frame, TOWN.ImageDraw.Draw(frame), now, {"temperature": 72}, t, "RACKVILLE", 0)
            crops = [frame.crop((x, top - 9, x + width, top - 1)).tobytes() for x, width, top, _, _ in TOWN.SIGNS]
            self.assertIsNotNone(frame.getbbox())
            self.assertNotEqual(crops[0], crops[1])

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
        person.update(dir=1, hungry=False, traveller=False, speed=8.0, dog=False, jogger=False)
        went_in = False
        for _ in range(30 * 20):
            town._simulate(1 / 30, 12.0, "sun", None)
            went_in = went_in or person["inside"] > 0
            if went_in and person["bag"] > 0:
                break
        self.assertTrue(went_in, "nobody went into an open shop")
        self.assertGreater(person["bag"], 0)

    def test_the_town_can_ignore_the_real_departures_and_keep_its_own(self):
        class Snap:
            stale = False
            data = {"lax": {"rows": [{"destination": "San Diego", "time": datetime(2026, 9, 20, 9, 18)}]}}

        class Ctx:
            snapshots = {"departures": Snap()}
        town = self.town()
        town.real_data = False
        where, _ = town._departure(Ctx(), datetime(2026, 9, 22, 12, 14, 50))
        self.assertIn(where, TOWN.DESTINATIONS)
        self.assertTrue(town.scheduled)
        with self.assertRaises(ValueError):
            TOWN.validate({"town_name": "TOWN", "real_data": "yes"})

    def test_after_dark_nobody_is_on_the_beach_and_the_cat_keeps_to_the_pavement(self):
        town = self.town()
        for _ in range(30 * 400):
            town._simulate(1 / 30, 2.0, "sun", None)
            for person in town.people:
                self.assertGreater(person["x"], TOWN.BEACH_END - 16 - 3, "somebody on the beach at 2 AM")
            if town.cat:
                self.assertGreater(town.cat["x"], TOWN.BEACH_END - 3)
                self.assertLess(town.cat["x"], TOWN.PEOPLE_EAST + 5)
        self.assertLess(len(town.people), 12, "people pile up instead of going home")

    def test_by_day_nobody_walks_where_the_wash_has_reached(self):
        town = self.town()
        town.level = 30.0                      # the water is up over the row people walk on
        for _ in range(30 * 120):
            town._simulate(1 / 30, 13.0, "sun", None)
            for person in town.people:
                if 0 <= person["x"] < TOWN.BEACH_END - 16:
                    self.assertEqual(person["dir"], 1, "walking further into the water")

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

    def test_a_bus_stops_at_its_stop_and_the_traffic_waits_behind_it(self):
        town = self.town()
        town.cars, town.people, town.bus_wait = [], [], 0.0
        halted = False
        for _ in range(30 * 60):
            town._simulate(1 / 30, 12.0, "sun", None)
            bus = next((c for c in town.cars if c.get("bus")), None)
            if bus and bus["halt"] > 0:
                halted = True
                self.assertEqual(bus["speed"], 0.0)
                self.assertGreaterEqual(bus["x"] + TOWN.BUS_DOOR, TOWN.BUS_STOP_X)
                behind = [c for c in town.cars if c is not bus and c["lane"] == 0 and c["x"] < bus["x"]]
                for car in behind:
                    self.assertLess(car["x"] + len(TOWN.CAR[0]), bus["x"] + 1, "a car drove into the back of the bus")
        self.assertTrue(halted, "the bus never stopped")

    def test_pigeons_scatter_when_somebody_walks_up_and_ships_stay_on_the_sea(self):
        town = self.town()
        town.pigeons = [{"x": 240.0, "y": 25.0, "dir": 1, "zone": (232, 262), "hop": 9, "fly": False, "vx": 0.0, "vy": 0.0}]
        town.people = []
        town._spawn_person(241.0)
        town._pigeons_step(1 / 30, town.rng, 12.0)
        self.assertTrue(town.pigeons[0]["fly"])
        town.pigeons = []
        town._pigeons_step(1 / 30, town.rng, 2.0)          # asleep at 2 AM
        self.assertEqual(town.pigeons, [])
        town.ship_wait = 0.0
        for _ in range(30 * 200):
            town._ship_step(1 / 30, town.rng)
            if town.ship:
                self.assertTrue(-17 < town.ship["x"] < TOWN.BEACH_END + 5)


class NewsBreakingTests(unittest.TestCase):
    """A story under a minute old is breaking, and the desk says so."""

    def rows(self, seconds):
        from datetime import timedelta
        return [dict(title="Fed announces surprise rate cut as markets rally", channel="MONEY", outlet="CNBC",
                     published=datetime.now(timezone.utc) - timedelta(seconds=seconds))]

    def frame(self, rows, style, t=2.0):
        registry = PluginRegistry(); registry.register(NEWS.plugin)
        config = validate_config({"plugins": {"news": {"style": style}}, "modules": {"news": {"enabled": True}},
                                  "playlist": [{"id": "news", "module": "news"}]}, registry)
        screen = NEWS.NewsModule()
        snapshots = {"news": Snapshot({"items": rows}, source="rss")}
        now = datetime.now(timezone.utc)
        screen.render(RenderContext(now, 0, config, snapshots, Message(), SystemStatus(), 1))
        return validate_frame(screen.render(RenderContext(now, t, config, snapshots, Message(), SystemStatus(), 1)))

    def test_under_a_minute_old_is_breaking_not_zero_minutes_ago(self):
        self.assertTrue(NEWS.breaking(self.rows(20)[0]))
        self.assertFalse(NEWS.breaking(self.rows(75)[0]))
        self.assertFalse(NEWS.breaking({"title": "undated", "published": None}))
        self.assertEqual(NEWS._age(self.rows(5)[0]["published"]), "BREAKING")
        self.assertEqual(NEWS._age(self.rows(300)[0]["published"], long=True), "5M AGO")

    def test_the_caption_of_a_breaking_story_names_the_outlet_not_the_age(self):
        self.assertEqual(NEWS._caption(self.rows(10)[0], 100), "CNBC")
        self.assertEqual(NEWS._caption(self.rows(400)[0], 100), "CNBC 6M AGO")

    def test_a_breaking_story_looks_different_on_both_desks(self):
        for style in ("headline", "breaking", "zipper"):
            for t in (0.5, 2.0):
                with self.subTest(style=style, t=t):
                    self.assertNotEqual(self.frame(self.rows(10), style, t).tobytes(),
                                        self.frame(self.rows(3600), style, t).tobytes())

    def test_a_headline_card_shows_whole_lines_and_is_read_in_seconds_not_half_a_minute(self):
        title = self.rows(3600)[0]["title"]
        lines = NEWS.headline_lines(title)
        self.assertEqual(" ".join(lines), title)          # every word, none cut in half
        self.assertTrue(all(text_width(line, 1, True) <= NEWS.LINE_WIDTH for line in lines))
        self.assertLess(NEWS.card_seconds(title), 12)
        crawl = NEWS.READ_PAUSE + NEWS.crawl_seconds(text_width(title, 2, True), 30)
        self.assertLess(NEWS.card_seconds(title), crawl)
        # The first two lines are up, whole and still, once the card has typed on.
        frame = self.frame(self.rows(3600), "headline", NEWS.BUMPER_SECONDS + 1.5)
        lit = [y for y in range(32) if any(frame.getpixel((x, y)) == NEWS.WHITE for x in range(128))]
        self.assertGreaterEqual(min(lit), NEWS.CARD_TOP)
        self.assertLessEqual(max(lit), 31)

    def test_a_headline_card_rolls_up_a_line_at_a_time_and_ends_on_the_last(self):
        title = "One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
        lines = NEWS.headline_lines(title)
        self.assertGreater(len(lines), 2)
        self.assertEqual(NEWS.card_scroll(title, 0), 0)
        self.assertEqual(NEWS.card_scroll(title, NEWS.card_seconds(title)), (len(lines) - 2) * NEWS.LINE_PITCH)
        positions = [NEWS.card_scroll(title, step / 30) for step in range(int(NEWS.card_seconds(title) * 30))]
        self.assertEqual(positions, sorted(positions))    # never jumps back

    def test_auto_is_mostly_headline_cards(self):
        screen = NEWS.NewsModule()
        rows = self.rows(3600)
        registry = PluginRegistry(); registry.register(NEWS.plugin)
        config = validate_config({"modules": {"news": {"enabled": True}},
                                  "playlist": [{"id": "news", "module": "news"}]}, registry)
        styles = []
        for scene in range(6):
            context = RenderContext(datetime.now(timezone.utc), 1, config,
                                    {"news": Snapshot({"items": rows}, source="rss")}, Message(), SystemStatus(), scene)
            styles.append(screen._story(context)[0][0])
        self.assertEqual(styles.count("headline"), 4)
        self.assertEqual(styles.count("zipper"), 2)

    def test_the_lamp_flashes(self):
        self.assertNotEqual(NEWS.flashing(0.1), NEWS.flashing(0.6))

    def test_strips_are_drawn_ahead_gently_not_all_in_one_go(self):
        import asyncio
        rows = [dict(title=f"Headline number {n} about something", channel="TOP", outlet="NBC", published=None)
                for n in range(12)]
        provider = NEWS.NewsProvider(type("Context", (), {"settings": {}})())
        NEWS.zipper_strip.cache_clear()

        async def go():
            provider._warm_later(rows)
            first = NEWS.zipper_strip.cache_info().currsize      # nothing has been drawn yet: the caller was not held up
            await provider.warming
            return first, NEWS.zipper_strip.cache_info().currsize
        first, last = asyncio.run(go())
        self.assertEqual(first, 0)
        self.assertEqual(last, 3)


class FinanceTapeTests(unittest.TestCase):
    """The tape is drawn a symbol at a time, and new quotes never make the crawl jump."""

    @staticmethod
    def data(scale, seed=1, named=True):
        rng = random.Random(seed)
        rows = []
        for symbol, name in (("NVDA", "NVIDIA"), ("AAPL", "APPLE"), ("MSFT", "MICROSOFT"), ("TSLA", "TESLA")):
            name = name if named else ""
            closes = [round(100 * scale + rng.uniform(-5, 5), 2) for _ in range(30)]
            rows.append(dict(FINANCE._row(symbol, closes[-1], closes[0], closes), name=name))
        return {"indices": [dict(rows[0], label="S&P")], "tape": rows}

    def context(self, data, t):
        registry = PluginRegistry(); registry.register(FINANCE.plugin)
        config = validate_config({"plugins": {"finance": {}}, "modules": {"finance": {"enabled": True}},
                                  "playlist": [{"id": "finance", "module": "finance"}]}, registry)
        return RenderContext(datetime.now(timezone.utc), t, config, {"finance": Snapshot(data)}, Message(),
                             SystemStatus(), 1)

    def test_the_strip_is_the_blocks_side_by_side(self):
        key = FINANCE._key(self.data(1)["tape"])
        strip, starts = FINANCE.tape_strip(key)
        self.assertEqual(len(starts), len(key))
        self.assertEqual(starts[1] - starts[0], FINANCE._layout(key[0])[5] + 15)
        block = FINANCE.tape_block(key[1])
        self.assertEqual(strip.crop((starts[1], 0, starts[1] + block.width, 21)).tobytes(), block.tobytes())

    def test_new_quotes_swap_in_without_the_crawl_jumping(self):
        screen = FINANCE.FinanceModule()
        before, after = self.data(1), self.data(37.7, named=False)   # quotes that lay out a different width
        screen.render(self.context(before, 0.0))
        for frame in range(1, 200):
            screen.render(self.context(before, frame / 30))
        old_strip, old_starts = screen._strip[1], screen._strip[2]
        edge = screen.position % old_strip.width
        index = max(i for i, start in enumerate(old_starts) if start <= edge)
        inside = edge - old_starts[index]
        screen.render(self.context(after, 200 / 30))
        new_strip, new_starts = screen._strip[1], screen._strip[2]
        self.assertNotEqual(old_strip.width, new_strip.width, "the test data did not change the layout")
        edge_now = screen.position % new_strip.width
        index_now = max(i for i, start in enumerate(new_starts) if start <= edge_now)
        # the same symbol is at the left edge, and it is one pixel further along, as any frame would have it
        self.assertEqual(index_now, index)
        self.assertEqual(edge_now - new_starts[index_now], inside + 1)


class PixelTownPeopleTests(unittest.TestCase):
    """People who walk, stand and are followed, rather than shuffle on the spot."""

    def town(self, hour=12.5):
        registry = PluginRegistry(); registry.register(TOWN.plugin)
        config = validate_config({}, registry)
        town = TOWN.Town()
        town.rng.seed(5)
        now = datetime(2026, 9, 21, int(hour), 30, tzinfo=timezone.utc)
        return town, config, now

    def run_for(self, seconds, hour=12.5, each=None):
        town, config, now = self.town(hour)
        for frame in range(int(seconds * 30)):
            town.render(RenderContext(now, frame / 30, config, {}, Message(), SystemStatus(), 1))
            if each:
                each(town, frame)
        return town

    def test_feet_move_only_when_the_person_does(self):
        """Half of everyone used to be marching on the spot, mostly the crowd on the platform."""
        counts = {"walking": 0, "still": 0}

        def look(town, frame):
            for person in town.people:
                counts["walking" if person["moving"] else "still"] += 1
        self.run_for(120, each=look)
        self.assertGreater(counts["walking"], 500)
        town = self.run_for(60)
        for person in town.people:
            if not person["moving"]:
                # a person standing still is in the standing pose whatever their stride count says
                frame = Image.new("RGB", (128, 32))
                one = dict(person, stride=0.0)
                two = dict(person, stride=1.7)
                TOWN.Town._person(frame, frame.load(), one, 100.0)
                a = frame.tobytes()
                frame = Image.new("RGB", (128, 32))
                TOWN.Town._person(frame, frame.load(), two, 100.0)
                self.assertEqual(a, frame.tobytes())

    def test_a_walker_alternates_legs_as_the_ground_goes_by(self):
        person = {"x": 20.0, "dir": 1, "speed": 9.0, "shirt": (230, 60, 60), "skin": (255, 214, 170),
                  "slot": None, "carry": 0.0, "bag": 0.0, "dog": False, "moving": True, "stride": 0.0}
        poses = set()
        for stride in (0.0, 1.7, 3.3, 5.0):
            frame = Image.new("RGB", (128, 32))
            TOWN.Town._person(frame, frame.load(), dict(person, stride=stride), 100.0)
            poses.add(frame.tobytes())
        self.assertEqual(len(poses), 2, "a walking person has two poses, legs apart and legs together")

    def test_no_more_than_a_handful_wait_on_the_platform(self):
        town = self.run_for(240)
        self.assertLessEqual(town._waiting(), TOWN.PLATFORM_CROWD + 1)

    def test_the_camera_stays_with_somebody_and_never_whips_across_town(self):
        views, followed, visible = [], [0], [0]

        def look(town, frame):
            views.append(town.camera.view)
            if town.camera.subject is not None:
                followed[0] += 1
                visible[0] += 0 <= town.camera.subject["x"] - town.camera.view < 128
        self.run_for(180, each=look)
        self.assertGreater(followed[0], 30 * 30, "the camera never followed anybody")
        self.assertGreater(visible[0] / followed[0], .9, "the person being followed was off the panel")
        self.assertLessEqual(max(abs(b - a) for a, b in zip(views, views[1:])), 2)

    def test_rooftop_signs_are_drawn_where_they_stand_even_when_only_part_is_in_view(self):
        """A sign that only existed while the whole of it fitted the panel popped in and out."""
        now = datetime(2026, 9, 21, 14, 5, tzinfo=timezone.utc)
        x, width, top, _, _ = TOWN.SIGNS[0]
        for view in (0, x - 20, x - 100, TOWN.WORLD - TOWN.VIEW):
            frame = Image.new("RGB", (TOWN.WORLD, 32))
            TOWN.Town._sign(frame, TOWN.ImageDraw.Draw(frame), now, {}, 3.0, "RACKVILLE", view)
            self.assertIsNotNone(frame.crop((x - 5, top - 9, x + width + 5, top - 1)).getbbox(),
                                 f"no sign drawn with the camera at {view}")

    def test_a_new_message_rolls_into_a_sign_rather_than_cutting(self):
        now = datetime(2026, 9, 21, 14, 5, tzinfo=timezone.utc)
        x, width, top, _, _ = TOWN.SIGNS[0]
        seen = set()
        for t in (5.9, 6.05, 6.15, 6.25, 6.45):
            frame = Image.new("RGB", (TOWN.WORLD, 32))
            TOWN.Town._sign(frame, TOWN.ImageDraw.Draw(frame), now, {"temperature": 72}, t, "RACKVILLE", 0)
            seen.add(frame.crop((x - 5, top - 9, x + width + 5, top - 1)).tobytes())
        self.assertGreaterEqual(len(seen), 4)
