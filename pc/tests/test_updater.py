import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import updater as u  # noqa: E402


def release(*names):
    return {"tag_name": "v2.0.42", "assets": [
        {"name": name, "browser_download_url": "https://example.test/" + name, "size": 1234}
        for name in names]}


class VersionTests(unittest.TestCase):
    def test_parses_and_pads_to_three_parts(self):
        self.assertEqual(u.parse_version("2.0"), (2, 0, 0))
        self.assertEqual(u.parse_version("v2.0.42"), (2, 0, 42))
        self.assertEqual(u.parse_version("2.0.42.9"), (2, 0, 42))

    def test_stops_at_the_first_non_number(self):
        self.assertEqual(u.parse_version("0.0.0-dev.7"), (0, 0, 0))
        self.assertEqual(u.parse_version("2.1-rc1"), (2, 1, 0))

    def test_rejects_what_isnt_a_version(self):
        for text in ("", None, "dev", "vNext"):
            self.assertIsNone(u.parse_version(text))

    def test_compares_numerically_not_as_text(self):
        self.assertTrue(u.is_newer("2.0.42", "2.0.9"))
        self.assertFalse(u.is_newer("2.0.9", "2.0.42"))

    def test_the_same_version_isnt_newer(self):
        self.assertFalse(u.is_newer("2.0.42", "2.0.42"))
        self.assertFalse(u.is_newer("2.0", "2.0.0"))

    def test_an_unparseable_version_is_never_newer(self):
        self.assertFalse(u.is_newer("dev", "2.0.0"))
        self.assertFalse(u.is_newer("2.0.1", "dev"))


class FindUpdateTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, u, "VERSION", u.VERSION)
        u.VERSION = "2.0.40"

    def test_builds_from_source_and_pull_requests_dont_check(self):
        for version in ("0.0.0", "0.0.0-dev.7"):
            u.VERSION = version
            self.assertFalse(u.checks_for_updates())
        u.VERSION = "2.0.42"
        self.assertTrue(u.checks_for_updates())

    def test_offers_a_newer_installer(self):
        found = u.find_update(release("OWQueue-2.0.42.apk", "OWQueue-Setup-2.0.42.exe"))
        self.assertEqual(found, ("2.0.42", "https://example.test/OWQueue-Setup-2.0.42.exe", 1234))

    def test_reads_the_version_from_the_asset_not_the_tag(self):
        # release.yml copies unchanged apps forward, so a newer tag can hold this build's installer.
        self.assertIsNone(u.find_update(release("OWQueue-Setup-2.0.40.exe")))

    def test_ignores_the_other_platforms_and_empty_releases(self):
        self.assertIsNone(u.find_update(release("OWQueue-2.0.42.apk", "OWQueue-2.0.42.ipa")))
        self.assertIsNone(u.find_update(release()))
        self.assertIsNone(u.find_update({}))


if __name__ == "__main__":
    unittest.main()
