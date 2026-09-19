"""The network keeper's decisions: it must never disturb a working connection."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("network_keeper", Path(__file__).resolve().parents[1] / "deploy/rackticker-network.py")
KEEPER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(KEEPER)


class KeeperDecisions(unittest.TestCase):
    def keeper(self, offline_for=None, setup=False):
        keeper = KEEPER.Keeper()
        keeper.offline_since = None if offline_for is None else 1000 - offline_for
        keeper.setup = setup
        return keeper

    def test_online_is_left_alone(self):
        self.assertEqual(self.keeper().decide(1000, True, ["home"]), "stay")
        self.assertEqual(self.keeper(offline_for=0).decide(1000, True, None), "stay")

    def test_a_short_dropout_is_waited_out(self):
        self.assertEqual(self.keeper(offline_for=120).decide(1000, False, ["home"]), "stay")
        self.assertEqual(self.keeper(offline_for=299).decide(1000, False, ["home"]), "stay")

    def test_setup_opens_after_five_minutes_away_or_quickly_on_first_boot(self):
        self.assertEqual(self.keeper(offline_for=300).decide(1000, False, ["home"]), "open")
        self.assertEqual(self.keeper(offline_for=90).decide(1000, False, []), "open")
        # Could not tell what is saved: take the gentle path.
        self.assertEqual(self.keeper(offline_for=120).decide(1000, False, None), "stay")

    def test_setup_closes_when_back_online_and_retries_the_saved_network(self):
        keeper = self.keeper(setup=True)
        self.assertEqual(keeper.decide(1000, True, ["home"]), "close")
        keeper.last_retry, keeper.last_visit = 1000 - 300, 0
        self.assertEqual(keeper.decide(1000, False, ["home"]), "retry")
        keeper.last_visit = 1000 - 30   # someone is on the setup page: do not pull it away
        self.assertEqual(keeper.decide(1000, False, ["home"]), "stay")
        keeper.request = ("home", "secret")
        self.assertEqual(keeper.decide(1000, False, ["home"]), "connect")

    def test_nmcli_escaped_fields(self):
        self.assertEqual(KEEPER.fields(r"802-11-wireless:Cafe\:Guest:wlan0"), ["802-11-wireless", "Cafe:Guest", "wlan0"])

    def test_setup_network_addresses_do_not_count_as_online(self):
        self.assertFalse(KEEPER.usable("10.42.0.1"))
        self.assertFalse(KEEPER.usable("169.254.3.4"))
        self.assertTrue(KEEPER.usable("192.168.4.170"))


class UnitTests(unittest.TestCase):
    def test_no_two_services_share_a_runtime_directory(self):
        """systemd re-owns a RuntimeDirectory when its unit starts: sharing one cost the
        display its permission to reach the panel."""
        import re
        seen = {}
        for unit in (Path(__file__).resolve().parents[1] / "deploy").glob("*.service"):
            for name in re.findall(r"^RuntimeDirectory=(\S+)", unit.read_text(), re.MULTILINE):
                self.assertNotIn(name, seen, f"{unit.name} and {seen.get(name)} share /run/{name}")
                seen[name] = unit.name


class QuickBootTests(unittest.TestCase):
    def test_three_quick_unplugs_clear_the_password(self):
        import tempfile
        folder = Path(tempfile.mkdtemp())
        KEEPER.QUICK_BOOTS, KEEPER.ACCESS = folder / "quick-boots", folder / "access.json"
        KEEPER.ACCESS.write_text("{}")
        self.assertEqual(KEEPER.count_quick_boot("boot-1"), "")
        self.assertEqual(KEEPER.count_quick_boot("boot-1"), "")   # a restart, not a power-up
        self.assertEqual(KEEPER.count_quick_boot("boot-2"), "")
        self.assertTrue(KEEPER.ACCESS.exists())
        self.assertEqual(KEEPER.count_quick_boot("boot-3"), "PASSWORD CLEARED")
        self.assertFalse(KEEPER.ACCESS.exists())
        self.assertEqual(KEEPER.QUICK_BOOTS.read_text(), "0 boot-3")
