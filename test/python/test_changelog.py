"""Tests for the changelog in the web UI (web/changelog.py). Run: python3 -m unittest discover -s test/python"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "backend"))

from strata_webui.core.ownership import Owner  # noqa: E402
from strata_webui.web import changelog  # noqa: E402

TEXT = """# Changelog

Intro with a [link](https://semver.org/).

## [v1.1.0] - 2026-12-01

Built on Strata `0.2.21`.

- New thing.

## [v1.0.10] - 2026-11-30

- Fix,
  over two lines.

## [v1.0.9] - 2026-11-29

- Old.
"""


class ParseTest(unittest.TestCase):
    def test_sections(self) -> None:
        entries = changelog.parse(TEXT)
        self.assertEqual([e["version"] for e in entries], ["1.1.0", "1.0.10", "1.0.9"])
        self.assertEqual(entries[0]["date"], "2026-12-01")
        self.assertEqual(entries[0]["body"], "Built on Strata `0.2.21`.\n\n- New thing.")
        self.assertNotIn("Intro", entries[0]["body"])

    def test_real_file(self) -> None:
        entries = changelog.parse((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertTrue(entries)
        self.assertTrue(all(changelog.is_release(e["version"]) and e["body"] for e in entries))

    def test_default_path_finds_the_file_in_a_checkout(self) -> None:
        self.assertEqual(changelog.default_path(), ROOT / "CHANGELOG.md")


class UnseenTest(unittest.TestCase):
    entries = changelog.parse(TEXT)

    def versions(self, current: str, seen: str | None) -> list[str]:
        return [e["version"] for e in changelog.unseen(self.entries, current, seen)]

    def test_every_version_since_the_last_seen(self) -> None:
        self.assertEqual(self.versions("1.1.0", "1.0.9"), ["1.1.0", "1.0.10"])  # 10 > 9, not text order

    def test_nothing_when_seen(self) -> None:
        self.assertEqual(self.versions("1.1.0", "1.1.0"), [])
        self.assertEqual(self.versions("1.0.9", "1.1.0"), [])               # a downgrade shows nothing

    def test_only_the_current_one_without_a_file(self) -> None:
        self.assertEqual(self.versions("1.0.10", None), ["1.0.10"])

    def test_never_for_dev_builds(self) -> None:
        self.assertEqual(self.versions("dev (f01ec60)", "1.0.9"), [])


class SeenFileTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.owner = Owner(os.getuid(), os.getgid())

    def test_fresh_install_is_marked(self) -> None:
        changelog.mark_fresh_install(self.dir, "1.1.0", has_configs=False, owner=self.owner)
        self.assertEqual(changelog.read_seen(self.dir), "1.1.0")

    def test_update_from_an_older_version_is_not_marked(self) -> None:
        changelog.mark_fresh_install(self.dir, "1.1.0", has_configs=True, owner=self.owner)
        self.assertIsNone(changelog.read_seen(self.dir))

    def test_existing_file_and_dev_builds_stay(self) -> None:
        changelog.write_seen(self.dir, "1.0.9", self.owner)
        changelog.mark_fresh_install(self.dir, "1.1.0", has_configs=False, owner=self.owner)
        self.assertEqual(changelog.read_seen(self.dir), "1.0.9")
        other = self.dir / "dev"
        other.mkdir()
        changelog.mark_fresh_install(other, "dev", has_configs=False, owner=self.owner)
        self.assertFalse((other / changelog.SEEN_FILE).exists())

    def test_garbage_in_the_file_counts_as_none(self) -> None:
        (self.dir / changelog.SEEN_FILE).write_text("<script>\n")
        self.assertIsNone(changelog.read_seen(self.dir))


if __name__ == "__main__":
    unittest.main()
