import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.core.config import ConfigStore, ConfigError, DEFAULT_CONFIG, validate_config


class ConfigTests(unittest.TestCase):
    def test_partial_config_merges_without_shared_mutation(self):
        raw = {"modules": {"clock": {"hour_format": "24"}}}
        c = validate_config(raw)
        self.assertEqual(c["modules"]["clock"]["hour_format"], "24")
        c["playlist"][0]["duration"] = 99
        self.assertEqual(DEFAULT_CONFIG["playlist"][0]["duration"], 8)
        full = copy.deepcopy(DEFAULT_CONFIG)
        result = validate_config(full)
        result["playlist"][0]["duration"] = 45
        self.assertEqual(full["playlist"][0]["duration"], 8)

    def test_bad_types_ranges_and_unknown_fields(self):
        cases = [[], {"version": True}, {"display": {"brightness": -1}},
                 {"display": {"fps": float("nan")}}, {"display": {"brightness": True}},
                 {"display": {"transition": "fade_magic"}}, {"modules": {"clock": {"enabled": "yes"}}},
                 {"simulator": {"mode": "retina"}}, {"playlist": []}, {"wat": 3},
                 {"modules": {"message": {"text": " "}}}, {"display": None}]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ConfigError): validate_config(raw)

    def test_duplicate_entry_ids_rejected_but_repeated_modules_allowed(self):
        c = validate_config({})
        self.assertEqual(sum(e["module"] == "clock" for e in c["playlist"]), 2)
        c["playlist"][1]["id"] = c["playlist"][0]["id"]
        with self.assertRaises(ConfigError): validate_config(c)

    def test_atomic_persistence_and_invalid_save_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConfigStore(Path(folder) / "config.json")
            c = store.load()
            c["modules"]["clock"]["hour_format"] = "24"
            store.save(c)
            self.assertEqual(store.load(), c)
            original = store.path.read_bytes()
            with self.assertRaises(ConfigError): store.save({"display": {"brightness": 200}})
            self.assertEqual(store.path.read_bytes(), original)
            with patch("app.core.config.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError): store.save(c)
            self.assertEqual(store.path.read_bytes(), original)
            self.assertEqual(list(Path(folder).glob("*.tmp")), [])

    def test_corrupt_file_has_clear_error_and_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConfigStore(Path(folder) / "config.json")
            store.path.write_text("{broken")
            with self.assertRaisesRegex(ConfigError, "Cannot load"): store.load()
            self.assertEqual(store.path.read_text(), "{broken")

    def test_existing_pre_tix_config_is_migrated_once(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConfigStore(Path(folder) / "config.json")
            old = copy.deepcopy(DEFAULT_CONFIG)
            old["modules"].pop("tixclock")
            old["playlist"] = [entry for entry in old["playlist"] if entry["module"] != "tixclock"]
            store.path.write_text(json.dumps(old))
            migrated = store.load()
            self.assertEqual(migrated["playlist"][1]["module"], "tixclock")
            self.assertEqual(sum(entry["module"] == "tixclock" for entry in migrated["playlist"]), 1)
            again = store.load()
            self.assertEqual(again, migrated)

    def test_sparse_existing_config_does_not_duplicate_default_tix_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ConfigStore(Path(folder) / "config.json")
            store.path.write_text("{}")
            loaded = store.load()
            self.assertEqual(sum(entry["module"] == "tixclock" for entry in loaded["playlist"]), 1)

    def test_example_is_current_and_valid(self):
        example = json.loads((Path(__file__).resolve().parents[1] / "config/config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_config(example), DEFAULT_CONFIG)

    def test_tixclock_settings_are_validated(self):
        self.assertEqual(DEFAULT_CONFIG["modules"]["tixclock"]["update_interval"], 4)
        self.assertEqual(DEFAULT_CONFIG["playlist"][1]["duration"], 20)
        self.assertEqual(validate_config({"modules": {"tixclock": {"hour_format": "24", "update_interval": 1, "show_label": False}}})["modules"]["tixclock"],
                         {"enabled": True, "hour_format": "24", "update_interval": 1, "show_label": False})
        for raw in ({"modules": {"tixclock": {"update_interval": 0}}},
                    {"modules": {"tixclock": {"update_interval": 61}}},
                    {"modules": {"tixclock": {"hour_format": "18"}}},
                    {"modules": {"tixclock": {"show_label": "yes"}}}):
            with self.assertRaises(ConfigError): validate_config(raw)
