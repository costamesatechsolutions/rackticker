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


if __name__ == "__main__":
    unittest.main()
