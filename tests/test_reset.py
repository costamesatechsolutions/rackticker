"""The resets, including the one that makes a card safe to clone and pass on.

Nothing here touches the running system: every command the script would run is
replaced, and the files it clears are made in a temporary folder.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("rackticker_reset",
                                              Path(__file__).resolve().parents[1] / "deploy/rackticker-reset.py")
RESET = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RESET)


class Ran:
    """Stands in for subprocess: remembers the commands, runs nothing."""

    def __init__(self, stdout=""):
        self.calls, self.stdout = [], stdout

    def __call__(self, *args, **kwargs):
        self.calls.append(list(args))
        return mock.Mock(returncode=0, stdout=self.stdout, stderr="")

    def ran(self, *needle):
        return any(list(needle) == call[:len(needle)] for call in self.calls)


class WifiTests(unittest.TestCase):
    LIST = ("802-11-wireless:Home Wi-Fi\n"
            "802-11-wireless:RackTicker-Setup\n"
            "802-3-ethernet:Wired connection 1\n"
            "802-11-wireless:Cafe\\:Free\n")

    def test_only_wireless_networks_are_listed_and_the_setup_one_is_not(self):
        with mock.patch.object(RESET, "run", Ran(self.LIST)):
            self.assertEqual(RESET.wifi_profiles(), ["Home Wi-Fi", "Cafe:Free"])

    def test_forgetting_deletes_each_saved_network(self):
        ran = Ran(self.LIST)
        with mock.patch.object(RESET, "run", ran):
            self.assertEqual(RESET.forget_wifi(), ["Home Wi-Fi", "Cafe:Free"])
        self.assertTrue(ran.ran("nmcli", "con", "delete", "id", "Home Wi-Fi"))
        # The device's own setup network is never deleted here: the keeper owns it.
        self.assertFalse(ran.ran("nmcli", "con", "delete", "id", "RackTicker-Setup"))

    def test_a_network_that_will_not_delete_is_not_claimed_as_forgotten(self):
        def stubborn(*args, **kwargs):
            listing = args[:3] == ("nmcli", "-t", "-e")
            return mock.Mock(returncode=0 if listing else 1, stdout=self.LIST if listing else "", stderr="")
        with mock.patch.object(RESET, "run", stubborn):
            self.assertEqual(RESET.forget_wifi(), [])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        home = self.folder / "home"
        (home).mkdir()
        (home / ".now-playing-spotify.json").write_text("{}")
        for name in ("config.json", "access.json", "quick-boots", "timezone",
                     "config.json.before-reset-20260101-000000"):
            (self.folder / name).write_text("x")
        for folder in ("plugins", "plugin-data"):
            (self.folder / folder).mkdir()
            (self.folder / folder / "something").write_text("x")
        self.patches = [mock.patch.object(RESET, "DATA", self.folder),
                        mock.patch.object(RESET, "HOME", home)]
        self.home = home
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def test_everything_the_owner_chose_is_cleared(self):
        removed = self.forget()
        self.assertIn("config.json", removed)
        self.assertIn("access.json", removed)
        for name in ("config.json", "access.json", "quick-boots", "timezone"):
            self.assertFalse((self.folder / name).exists(), name)
        # Backups of earlier resets go too: they hold the same settings.
        self.assertEqual(list(self.folder.glob("config.json.before-reset-*")), [])
        # The plugin folders stay, empty, so the app finds them where it expects.
        for folder in ("plugins", "plugin-data"):
            self.assertTrue((self.folder / folder).is_dir())
            self.assertEqual(list((self.folder / folder).iterdir()), [])

    def test_a_plugin_login_left_in_the_home_folder_goes_too(self):
        self.forget()
        self.assertFalse((self.home / ".now-playing-spotify.json").exists())

    def forget(self):
        with mock.patch("shutil.chown"):
            return RESET.forget_settings()


class IdentityTests(unittest.TestCase):
    def test_the_name_comes_from_this_pi_and_is_this_pi_s_own(self):
        with mock.patch.object(RESET, "cpu_serial", lambda: "100000001a2b3c4d"):
            self.assertEqual(RESET.device_name(), "rackticker-3c4d")

    def test_a_pi_that_does_not_say_still_gets_a_name_of_its_own(self):
        with mock.patch.object(RESET, "cpu_serial", lambda: ""):
            first, second = RESET.device_name(), RESET.device_name()
        self.assertTrue(first.startswith("rackticker-"))
        self.assertNotEqual(first, second)   # random, so two cloned cards still differ

    def test_a_serial_of_zeroes_is_not_trusted(self):
        with mock.patch.object(RESET, "cpu_serial", lambda: "0000000000000000"):
            self.assertNotEqual(RESET.device_name(), "rackticker-0000")


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(RESET, "STATE", self.folder),
                        mock.patch.object(RESET, "REQUEST", self.folder / "request.json"),
                        mock.patch.object(RESET, "STATUS", self.folder / "status.json")]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def ask(self, body):
        (self.folder / "request.json").write_text(body)
        with mock.patch.object(RESET, "reset") as reset:
            reset.return_value = 0
            RESET.request()
        return reset

    def test_the_request_is_carried_out_once_and_then_gone(self):
        reset = self.ask(json.dumps({"scope": "network"}))
        reset.assert_called_once_with("network")
        self.assertFalse((self.folder / "request.json").exists())

    def test_nonsense_is_refused_and_still_clears_the_request(self):
        for body in ('{"scope": "../../etc"}', '{"scope": "DROP TABLE"}', "not json"):
            (self.folder / "request.json").write_text(body)
            with mock.patch.object(RESET, "reset") as reset:
                RESET.request()
            reset.assert_not_called()
            self.assertFalse((self.folder / "request.json").exists(), body)

    def test_an_unknown_scope_changes_nothing(self):
        with mock.patch.object(RESET, "forget_settings") as settings, \
             mock.patch.object(RESET, "forget_wifi") as wifi, \
             mock.patch.object(RESET, "clear_identity") as identity:
            self.assertEqual(RESET.reset("burn"), 1)
        settings.assert_not_called()
        wifi.assert_not_called()
        identity.assert_not_called()

    def test_forgetting_wifi_leaves_the_settings_alone(self):
        with mock.patch.object(RESET, "forget_settings") as settings, \
             mock.patch.object(RESET, "forget_wifi", return_value=["Home"]):
            self.assertEqual(RESET.reset("network"), 0)
        settings.assert_not_called()

    def test_a_unit_being_passed_on_loses_its_identity_and_powers_off(self):
        with mock.patch.object(RESET, "forget_settings", return_value=[]), \
             mock.patch.object(RESET, "forget_wifi", return_value=[]), \
             mock.patch.object(RESET, "clear_identity") as identity, \
             mock.patch.object(RESET, "wipe_traces") as traces, \
             mock.patch("subprocess.run") as run:
            self.assertEqual(RESET.reset("ship"), 0)
        identity.assert_called_once()
        traces.assert_called_once()
        self.assertEqual(run.call_args[0][0][:2], ["systemctl", "poweroff"])

    def test_a_full_reset_keeps_the_device_itself_intact(self):
        with mock.patch.object(RESET, "forget_settings", return_value=[]), \
             mock.patch.object(RESET, "forget_wifi", return_value=[]), \
             mock.patch.object(RESET, "clear_identity") as identity, \
             mock.patch("subprocess.run"):
            self.assertEqual(RESET.reset("everything"), 0)
        identity.assert_not_called()   # still the same device; it just knows nothing


class WebRequestTests(unittest.TestCase):
    """What the control page may ask for, and what it may not ask for by mistake."""

    def setUp(self):
        from app.web import software_api
        self.api = software_api
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "reset").mkdir()
        self.request = mock.Mock()
        self.request.app = {"path": self.folder / "config.json"}
        software_api.KEYS["path"] = "path"

    def test_a_pi_offers_every_reset_and_a_laptop_only_the_safe_one(self):
        self.assertEqual([choice["scope"] for choice in self.api.reset_choices(self.request)],
                         ["settings", "network", "everything", "ship"])
        (self.folder / "reset").rmdir()
        self.assertEqual([choice["scope"] for choice in self.api.reset_choices(self.request)], ["settings"])

    def test_a_deep_reset_is_handed_to_the_root_helper_and_not_done_here(self):
        self.api.ask_root_to_reset(self.request, "ship")
        asked = json.loads((self.folder / "reset/request.json").read_text())
        self.assertEqual(asked["scope"], "ship")

    def test_a_copy_that_cannot_reset_wifi_says_so_rather_than_pretending(self):
        (self.folder / "reset").rmdir()
        with self.assertRaises(ValueError):
            self.api.ask_root_to_reset(self.request, "network")


if __name__ == "__main__":
    unittest.main()
