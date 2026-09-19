import importlib.util
from datetime import datetime
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("traffic_test", ROOT / "plugins/traffic/rackticker_traffic.py")
TRAFFIC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(TRAFFIC)


def log(when, kind, location, area, latlon="33745500:117867700"):
    return (f'<Log ID = "x"><LogTime>"{when}"</LogTime><LogType>"{kind}"</LogType>'
            f'<Location>"{location}"</Location><LocationDesc>""</LocationDesc><Area>"{area}"</Area>'
            f'<ThomasBrothers>""</ThomasBrothers><LATLON>"{latlon}"</LATLON><LogDetails><details>'
            f'<IncidentDetail>"[1] PRIVATE TEXT"</IncidentDetail></details></LogDetails></Log>')


FEED = (
    '<?xml version="1.0" ?><State><Center ID = "BCHB"><Dispatch ID = "OCCC">'
    + log("Sep 15 2026  2:52PM", "1125-Traffic Hazard", "I405 S / Beach Blvd", "Orange County FSP")
    + log("Sep 15 2026  2:53PM", "1125-Traffic Hazard", "I405 S / Beach Blvd", "Westminster")
    + log("Sep 15 2026  2:40PM", "1183-Trfc Collision-Unkn Inj", "Sr22 E / The City Dr", "Santa Ana")
    + log("Sep 15 2026  2:41PM", "Assist CT with Maintenance", "Sr55 N / Dyer Rd", "Santa Ana")
    + log("Sep 15 2026  2:42PM", "1182-Trfc Collision-No Inj", "Harbor Blvd / Adams Ave", "Costa Mesa")
    + log("Sep 15 2026  2:30PM", "1179-Trfc Collision-1141 Enrt", "I5 N / Jamboree Rd Onr", "Santa Ana", "34100000:118300000")
    + '</Dispatch></Center><Center ID = "LAHB"><Dispatch ID = "LACC">'
    + log("Sep 15 2026  2:52PM", "SIG Alert", "I10 E / Vermont Ave", "Central LA")
    + '</Dispatch></Center></State>'
)


class TrafficTests(unittest.TestCase):
    def test_feed_keeps_freeway_incidents_for_chosen_center_and_dedupes_patrol_logs(self):
        incidents = TRAFFIC.parse_feed(FEED, {"OCCC"})

        self.assertEqual([(row["kind"], row["system"], row["route"]) for row in incidents],
                         [("CRASH", "I", "5"), ("CRASH", "SR", "22"), ("HAZARD", "I", "405")])
        hazard = incidents[2]
        self.assertEqual((hazard["direction"], hazard["cross"], hazard["patrol"]), ("SB", "BEACH BLVD", False))
        self.assertEqual(incidents[0]["detail"], "INJURIES")
        self.assertEqual(incidents[0]["cross"], "JAMBOREE RD ON-RAMP")
        self.assertNotIn("PRIVATE", repr(incidents))

    def test_home_radius_filters_distant_incidents(self):
        incidents = TRAFFIC.parse_feed(FEED, {"OCCC"}, home=(33.7455, -117.8677), radius_miles=10)

        self.assertNotIn("5", [row["route"] for row in incidents])

    def test_age_labels_stay_short(self):
        now = datetime(2026, 9, 15, 16, 5, tzinfo=ZoneInfo("America/Los_Angeles"))
        at = datetime(2026, 9, 15, 14, 52, tzinfo=ZoneInfo("America/Los_Angeles"))
        self.assertEqual(TRAFFIC.age_label(at, now), "1H13")
        self.assertEqual(TRAFFIC.age_label(now, now), "NOW")
        self.assertEqual(TRAFFIC.age_label(at, now.replace(day=16, hour=10)), "19H")

    def test_old_logs_are_hidden(self):
        incidents = TRAFFIC.parse_feed(FEED, {"OCCC"})
        now = datetime(2026, 9, 15, 17, 35, tzinfo=ZoneInfo("America/Los_Angeles"))

        self.assertEqual([row["route"] for row in TRAFFIC.recent(incidents, now, 3)], ["22", "405"])


class TrafficRenderTests(unittest.TestCase):
    def test_healthy_empty_feed_reads_as_clear_not_missing(self):
        from app.core.config import validate_config
        from app.core.models import Snapshot, Message, SystemStatus
        from app.core.plugins import PluginRegistry
        from app.core.renderer import validate_frame
        from app.modules.base import RenderContext

        registry = PluginRegistry()
        registry.register(TRAFFIC.plugin)
        config = validate_config({"plugins": {"traffic": {}}, "modules": {"traffic": {"enabled": True}},
                                  "playlist": [{"id": "t", "module": "traffic"}]}, registry)
        module = TRAFFIC.TrafficModule()
        context = RenderContext(datetime.now(ZoneInfo("America/Los_Angeles")), 1, config,
                                {"traffic": Snapshot([])}, Message("X", "X"), SystemStatus())

        frame = validate_frame(module.render(context))

        self.assertFalse(module.available(context))
        self.assertTrue(any(frame.getpixel((x, y))[1] > 150 and frame.getpixel((x, y))[0] < 120
                            for x in range(68, 128) for y in range(10, 27)))


if __name__ == "__main__":
    unittest.main()


class CaltransTests(unittest.TestCase):
    def sign(self, number, route, lines, latitude=33.7, display="1 Page"):
        return {"cms": {"location": {"locationName": f"ZCMS {number} - X", "latitude": str(latitude),
                                     "longitude": "-117.9", "route": route, "direction": "South",
                                     "nearbyPlace": "Costa Mesa"},
                        "inService": "True", "message": {"display": display, "phase1": {
                            "phase1Line1": lines[0], "phase1Line2": lines[1], "phase1Line3": lines[2]}}}}

    def test_signs_nearby_freeways_and_templates(self):
        payload = {"data": [self.sign(1, "I-405", ["RIGHT LANES", "CLOSED", "AT MACARTHUR BL"]),
                            self.sign(2, "SR-55", ["MINUTES TO:", "RTE 514", ""]),
                            self.sign(3, "SR-73", ["", "", ""], display="Blank"),
                            self.sign(4, "I-10", ["FAR AWAY", "", ""], latitude=34.5)]}
        signs, routes, positions = TRAFFIC.parse_signs([payload], (33.7, -117.9), 10)
        self.assertEqual([sign["phases"][0][0] for sign in signs], ["RIGHT LANES"])
        self.assertEqual(routes[:3], [("I", "405"), ("SR", "55"), ("SR", "73")])
        travel = {"data": [{"tt": {"location": {"travelFlowDirection": "North",
                                                "begin": {"beginFreeFormDescription": "CMS 2", "beginRoute": "SR-55",
                                                          "beginLocationName": "A"},
                                                "end": {"endLocationName": "405 FWY"}},
                                   "traveltime": {"calculatedTraveltime": "600"}}}]}
        times = TRAFFIC.parse_travel_times([travel], positions, (33.7, -117.9), 10)
        self.assertEqual((times[0]["route"], times[0]["to"], times[0]["seconds"]), (("SR", "55"), "RTE 405", 600))
        # Written the way the signs write it, and never "to" the freeway it is on.
        self.assertEqual(TRAFFIC.destination("74 FWY"), "RTE 74")
        self.assertEqual(TRAFFIC.destination("JWA"), "AIRPORT")
        travel["data"][0]["tt"]["location"]["end"]["endLocationName"] = "55 FWY"
        self.assertEqual(TRAFFIC.parse_travel_times([travel], positions, (33.7, -117.9), 10), [])
