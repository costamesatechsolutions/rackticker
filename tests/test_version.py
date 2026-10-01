"""The version is written in three places; a release publishes all three as one."""
from pathlib import Path
import re
import unittest

from app import __version__

ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_pyproject_matches_the_package(self):
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertEqual(re.search(r'^version = "(.+)"', pyproject, re.M).group(1), __version__)

    def test_changelog_has_a_section_for_the_version(self):
        # tools/publish_release.sh uses this section as the release notes.
        self.assertIn(f"\n## {__version__} - ", (ROOT / "CHANGELOG.md").read_text())


class UpdateOfferTests(unittest.TestCase):
    """The page offers an update only when GitHub says it is newer than what is installed."""

    def test_an_older_release_is_not_offered_to_a_pi_running_ahead_of_it(self):
        import asyncio
        from app.web import software_api
        a, b = "a" * 40, "b" * 40
        software_api._ahead.update({(a, b): False, (b, a): True})
        self.assertFalse(asyncio.run(software_api.is_newer(a, b)))
        self.assertTrue(asyncio.run(software_api.is_newer(b, a)))
        self.assertFalse(asyncio.run(software_api.is_newer(a, a)))


if __name__ == "__main__":
    unittest.main()
