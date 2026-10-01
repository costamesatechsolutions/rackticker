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

    def test_update_installs_the_newest_release_not_the_commit_the_page_showed(self):
        import asyncio, json, tempfile
        from unittest import mock
        from app.web import software_api
        folder = Path(tempfile.mkdtemp())
        newest, stale, installed = "c" * 40, "d" * 40, "e" * 40
        request = mock.Mock()
        request.json = mock.AsyncMock(return_value={"commit": stale})
        with mock.patch.object(software_api, "updatable", return_value=True), \
                mock.patch.object(software_api, "update_dir", return_value=folder), \
                mock.patch.object(software_api, "revision", return_value=installed), \
                mock.patch.object(software_api, "latest", mock.AsyncMock(return_value={"commit": newest})):
            software_api._ahead[(installed, newest)] = True
            asyncio.run(software_api.software_update(request))
            self.assertEqual(json.loads((folder / "request.json").read_text())["commit"], newest)
            software_api._ahead[(installed, newest)] = False
            with self.assertRaises(ValueError):
                asyncio.run(software_api.software_update(request))


if __name__ == "__main__":
    unittest.main()
